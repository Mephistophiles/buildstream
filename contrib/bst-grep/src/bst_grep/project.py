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
"""Public bst queries and per-element source trees; no BuildStream Python imports."""

from collections import deque
from contextlib import contextmanager
from pathlib import Path
import tempfile
import uuid

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .process import execute


class Project:
    def __init__(self, directory=None, options=(), *, run=execute):
        self.base = ["bst", "--no-colors", "--strict"]
        if directory is not None:
            self.base += ["-C", str(directory)]
        for name, value in options:
            self.base += ["--option", name, value]
        self.run = run

    def report(self, targets, fields, deps="none"):
        """Frame multiline show fields without treating user data as delimiters."""
        marker = "BST_GREP_" + uuid.uuid4().hex + "_"
        format_string = marker + "record\n"
        for field in fields:
            format_string += marker + field + "\n%{" + field + "}\n"
        format_string += marker + "end\n"
        output = self.run(
            self.base
            + [
                "show",
                "--deps",
                deps,
                "--order",
                "alpha",
                "--format",
                format_string,
                "--",
                *targets,
            ]
        )
        sections = output.split(marker + "record\n")
        if sections[0].strip():
            raise ValueError("Unexpected output before bst show records")
        records = []
        for section in sections[1:]:
            record = {}
            for index, field in enumerate(fields):
                prefix = marker + field + "\n"
                if not section.startswith(prefix):
                    raise ValueError(f"Malformed bst show field: {field}")
                section = section[len(prefix) :]
                boundary = marker + (fields[index + 1] if index + 1 < len(fields) else "end") + "\n"
                value, separator, remainder = section.partition(boundary)
                if not separator or marker in value:
                    raise ValueError(f"Missing bst show boundary for {field}")
                if value.strip() == "%{" + field + "}":
                    raise ValueError(f"Installed BuildStream does not support show field: {field}")
                record[field] = value
                section = boundary + remainder
            if section.strip() != marker + "end":
                raise ValueError("Unexpected trailing bst show output")
            records.append(record)
        return records

    def select(self, targets, deps, *, workspaces=False):
        fields = ("name", "workspace-dirs") if workspaces else ("name",)
        selected = {}
        for record in self.report(targets, fields, deps):
            name = record["name"].strip()
            if not name or "\n" in name or name in selected:
                raise ValueError("Invalid or duplicate element name from bst show")
            selected[name] = record.get("workspace-dirs", "").rstrip("\n")
        return selected

    def paths(self, targets, deps="all"):
        roots = list(self.select(targets, "none"))
        adjacency = {}
        build_edges = {}
        yaml = YAML(typ="safe")
        for record in self.report(targets, ("name", "build-deps", "runtime-deps"), "all"):
            dependencies = {}
            for field in ("build-deps", "runtime-deps"):
                try:
                    values = yaml.load(record[field])
                except YAMLError as error:
                    raise ValueError(f"Malformed {field} from bst show: {error}") from error
                if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                    raise ValueError(f"Invalid {field} from bst show")
                dependencies[field] = set(values)
            name = record["name"].strip()
            if not name or name in adjacency:
                raise ValueError("Invalid or duplicate element in dependency graph")
            build_edges[name] = sorted(dependencies["build-deps"])
            adjacency[name] = sorted(
                dependencies["build-deps"] | dependencies["runtime-deps"]
                if deps == "all"
                else dependencies["runtime-deps"]
            )
        if any(root not in adjacency for root in roots) or any(
            dep not in adjacency for children in (*adjacency.values(), *build_edges.values()) for dep in children
        ):
            raise ValueError("Dependency graph changed while loading; retry the search")
        # One BFS per root, not per matched line. Store predecessors rather than
        # eagerly materializing every path in a large shared dependency graph.
        root_edges = None
        if deps == "build":
            root_edges = {root: build_edges[root] for root in roots}
        elif deps == "none":
            root_edges = {root: [] for root in roots}
        return Paths(adjacency, roots, root_edges)

    @contextmanager
    def sources(self, name, workspace):
        if workspace:
            prefix = "Workspace: "
            if not workspace.startswith(prefix):
                raise ValueError("Unexpected workspace-dirs output from BuildStream")
            root = Path(workspace[len(prefix) :])
            if not root.is_absolute() or not root.is_dir():
                raise ValueError(f"Workspace directory is unavailable: {root}")
            yield root.resolve(), "workspace"
            return
        with tempfile.TemporaryDirectory(prefix="bst-grep-sources-") as temporary:
            destination = Path(temporary) / "sources"
            self.run(
                self.base
                + [
                    "source",
                    "checkout",
                    "--deps",
                    "none",
                    "--directory",
                    str(destination),
                    "--",
                    name,
                ]
            )
            yield checkout_root(destination, name), "checkout"

    @contextmanager
    def configuration(self, name):
        records = self.report([name], ("config", "vars", "env"))
        if len(records) != 1:
            raise ValueError("Expected one configuration record from bst show")
        with tempfile.TemporaryDirectory(prefix="bst-grep-config-") as temporary:
            root = Path(temporary)
            for field, content in records[0].items():
                # bst show adds a framing newline; preserve the field's own lines.
                (root / (field + ".yaml")).write_text(content.removesuffix("\n"), encoding="utf-8")
            yield root, "resolved"


class Paths:
    def __init__(self, adjacency, roots, root_edges=None):
        self.predecessors = []
        for root in roots:
            parents = {root: None}
            queue = deque([root])
            while queue:
                parent = queue.popleft()
                children = root_edges[root] if root_edges is not None and parent == root else adjacency[parent]
                for child in children:
                    if child not in parents:
                        parents[child] = parent
                        queue.append(child)
            self.predecessors.append(parents)

    def to(self, name):
        paths = []
        for parents in self.predecessors:
            if name not in parents:
                continue
            path, current = [], name
            while current is not None:
                path.append(current)
                current = parents[current]
            paths.append(list(reversed(path)))
        return paths


def checkout_root(directory, name):
    """Remove bst's element/junction wrapper directories, never a source directory."""
    root = directory
    for _ in name.split(":"):
        entries = list(root.iterdir())
        if not entries and root == directory:
            return root  # Source-less elements have an empty checkout.
        if len(entries) != 1 or entries[0].is_symlink() or not entries[0].is_dir():
            raise ValueError("Unexpected bst source checkout layout")
        root = entries[0]
    return root
