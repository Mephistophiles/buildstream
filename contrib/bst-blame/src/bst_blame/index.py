#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Build a query-specific ownership index from local artifact listings."""

import fnmatch
from bst_tools_core.paths import normalize_path
import re

EMPTY = "This element has no associated artifacts"
STATS = re.compile(r"^([dl-][rwx-]{9}) +(dir|link|exe|reg) +([0-9]+)(.*)$")


def sections(output, aliases):
    result, current = {}, None
    for line in output.splitlines():
        if line.startswith("\t"):
            if current is None:
                raise ValueError("Artifact entry before its header")
            result[current].append(line[1:])
        elif line.startswith("  ") and line.endswith(":"):
            alias = line[2:-1]
            if alias not in aliases or alias in result:
                raise ValueError("Unknown or duplicate artifact listing header")
            current = alias
            result[current] = []
        elif line:
            raise ValueError("Malformed artifact listing; line-break filenames are unsupported")
    return result


def parse_listings(plain, detailed, names):
    aliases = {name.rsplit(":", 1)[-1]: name for name in names}
    if len(aliases) != len(names):
        raise ValueError("A listing batch must have unique unqualified element names")
    paths, metadata = sections(plain, aliases), sections(detailed, aliases)
    if paths.keys() != metadata.keys():
        raise ValueError("Artifact listing changed between queries")
    result = {}
    for alias, raw_paths in paths.items():
        rows = metadata[alias]
        if raw_paths == rows == [EMPTY]:
            result[aliases[alias]] = []
            continue
        if not raw_paths or len(raw_paths) != len(rows):
            raise ValueError("Artifact listing changed between queries")
        entries, seen = [], set()
        for raw_path, row in zip(raw_paths, rows):
            match = STATS.fullmatch(row)
            if match is None:
                raise ValueError("Unsupported artifact listing metadata")
            mode, kind, size, tail = match.groups()
            # BuildStream pads the size column to width 11, then adds one space.
            # Strip only that padding, preserving spaces at the start of names.
            padding = " " * (max(0, 11 - len(size)) + 1)
            if not tail.startswith(padding):
                raise ValueError("Unsupported artifact size column")
            body = tail[len(padding) :]
            target = None
            if kind == "link":
                prefix = raw_path + " -> "
                if not body.startswith(prefix):
                    raise ValueError("Symlink listing changed between queries")
                target = body[len(prefix) :]
            elif body != raw_path:
                raise ValueError("Artifact path changed between queries")
            expected_mode = {"dir": "d", "link": "l", "exe": "-", "reg": "-"}[kind]
            if mode[0] != expected_mode:
                raise ValueError("Inconsistent artifact file type")
            path = normalize_path(raw_path)
            if path in seen:
                raise ValueError("Duplicate artifact path")
            seen.add(path)
            entries.append(
                {
                    "path": path,
                    "kind": {"dir": "directory", "link": "symlink"}.get(kind, "file"),
                    "size": int(size),
                    "executable": kind == "exe",
                    "link_target": target,
                }
            )
        result[aliases[alias]] = entries
    return result


def batches(names, limit=64):
    batch, aliases = [], set()
    for name in sorted(names):
        alias = name.rsplit(":", 1)[-1]
        if alias in aliases or len(batch) >= limit:
            yield batch
            batch, aliases = [], set()
        batch.append(name)
        aliases.add(alias)
    if batch:
        yield batch


def collect(project, graph, pattern, glob=False):
    index, inspected, missing, errors, skipped = {}, set(), [], [], []
    candidates = []
    for name in sorted(graph.selected):
        node = graph.nodes[name]
        if node["kind"] in ("junction", "stack"):
            skipped.append(name)
        elif node["state"] in ("cached", "failed"):
            candidates.append(name)
        else:
            missing.append({"element": name, "state": node["state"]})

    def inspect(names):
        try:
            contents = parse_listings(*project.listings(names), names)
        except (OSError, ValueError) as error:
            if len(names) > 1:
                # Isolate stale/deleted cache entries without losing good owners.
                middle = len(names) // 2
                inspect(names[:middle])
                inspect(names[middle:])
                return
            errors.append({"element": names[0], "message": str(error)})
            return
        for name in names:
            if name not in contents:
                errors.append({"element": name, "message": "Artifact disappeared from the local cache"})
                continue
            record(name, contents[name])

    def record(name, entries):
        inspected.add(name)
        dependency_paths = graph.paths(name)
        for entry in entries:
            path = entry["path"]
            if not (fnmatch.fnmatchcase(path, pattern) if glob else path == pattern):
                continue
            owner = {key: value for key, value in entry.items() if key != "path"}
            owner.update(
                element=name,
                artifact_key=graph.nodes[name]["full-key"],
                state=graph.nodes[name]["state"],
                dependency_paths=dependency_paths,
            )
            index.setdefault(path, []).append(owner)

    if getattr(project, "cas_index", None) is not None:
        from bst_cas import load_artifacts

        keys = {name: graph.nodes[name]["full-key"] for name in candidates}
        for name, entries, identity, error in load_artifacts(project.cas_index, project, candidates, keys):
            if error:
                errors.append({"element": name, "message": error})
            else:
                record(name, entries)
    else:
        for batch in batches(candidates):
            inspect(batch)
    return index, {
        "selected": len(graph.selected),
        "inspected": len(inspected),
        "missing": missing,
        "errors": errors,
        "skipped": skipped,
        "complete": not missing and not errors,
    }
