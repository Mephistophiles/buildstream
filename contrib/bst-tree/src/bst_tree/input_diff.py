#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Changed resolved inputs with independently captured sources on each side."""

import json

from .model import DETAIL_FIELDS
from .provenance import lookup, pointer


def changed_paths(before, after, prefix=""):
    # Lists are one input: positional matching after insertion would imply false
    # correspondence between different commands. Preserve their own leaf origins.
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            path = prefix + "/" + pointer(key)
            if key not in before or key not in after:
                yield path
            else:
                yield from changed_paths(before[key], after[key], path)
    elif before != after:
        yield prefix


def side(graph, name, path, history, entries):
    present, value = (
        lookup(graph.details[name], path) if name in graph.nodes and graph.details is not None else (False, None)
    )
    result = {"present": present, "value": value, "sources": [], "status": "absent"}
    if not present:
        return result
    if graph.provenance is None:
        result["status"] = "not-captured"
        return result
    data = graph.provenance[name]
    # Prefer a captured list/container origin. Otherwise retain all descendant
    # origins and references, rather than inventing one winning source for a list.
    requested = {path} if path in entries else {p for p in entries if p.startswith(path + "/")}
    selected = set(requested)
    descendants = {p for p in entries if p.startswith(path + "/")} if isinstance(value, (dict, list)) else set()
    pending = list(requested | descendants)
    while pending:
        for ref in entries[pending.pop()]["references"]:
            if ref["path"] in entries and ref["path"] not in selected:
                selected.add(ref["path"])
                pending.append(ref["path"])
    result["status"] = "captured" if requested else "unavailable"
    result["history_available"] = data["history_available"]
    result["sources"] = [
        dict(
            {k: v for k, v in entries[p].items() if history or k != "history"},
            selected=p in requested,
            conditions=[
                c
                for step in entries[p].get("history", [])
                if step["state"] in ("effective", "contributes")
                for c in step.get("conditions", [])
            ],
        )
        for p in sorted(selected)
    ]
    return result


def input_changes(old, new, name, *, history=False, limit=None):
    before = old.details.get(name, {}) if old.details is not None else {}
    after = new.details.get(name, {}) if new.details is not None else {}
    paths = list(changed_paths(before, after))
    # Provenance supplements the resolved comparison; never compare a missing
    # details section as though it were an empty configuration.
    if (name in old.nodes and old.details is None) or (name in new.nodes and new.details is None):
        return [], 0
    selected = paths if limit is None else paths[:limit]
    indexes = [
        (
            {entry["path"]: entry for entry in graph.provenance[name]["entries"]}
            if graph.provenance is not None and name in graph.nodes
            else {}
        )
        for graph in (old, new)
    ]
    return [
        {
            "path": path,
            "old": side(old, name, path, history, indexes[0]),
            "new": side(new, name, path, history, indexes[1]),
        }
        for path in selected
        if path.split("/")[1] in DETAIL_FIELDS
    ], len(paths)


def location(origin):
    if origin.get("synthetic"):
        return "synthetic/generated"
    return f"{origin.get('file')}:{origin.get('line')}:{origin.get('column')}"


def brief(value, limit=180):
    text = json.dumps(value, ensure_ascii=True, sort_keys=True)
    return text if limit is None or len(text) <= limit else text[:limit] + "…"


def render_inputs(changes, *, history=False, limit=3, source_limit=3):
    lines = []
    for change in changes if limit is None else changes[:limit]:
        lines.append("  " + change["path"])
        for key, label in (("old", "Before"), ("new", "After")):
            data = change[key]
            lines.append(f"    {label}: " + (brief(data["value"]) if data["present"] else "[absent]"))
            if data["status"] == "not-captured":
                lines.append("      Source not captured; use snapshot --provenance.")
            sources = data["sources"]
            for source in sources if source_limit is None else sources[:source_limit]:
                tag = "source" if source["selected"] else "variable evidence"
                lines.append(
                    f"      {source['path']} [{tag}]: {location(source['origin'])}; raw {brief(source['raw'])}"
                )
                steps = source.get("history", [])
                # Keep applied conditions visible without expanding the history.
                for condition in source.get("conditions", []):
                    lines.append(
                        f"        Applied condition: {brief(condition['expression'])} @ {location(condition['origin'])}"
                    )
                if history:
                    for step in steps:
                        conditions = ", ".join(brief(c["expression"]) for c in step.get("conditions", []))
                        lines.append(
                            f"        {step['state']} ({step['operation']}): {brief(step['raw'])} @ {location(step['origin'])}"
                            + (f" [if {conditions}]" if conditions else "")
                        )
            if source_limit is not None and len(sources) > source_limit:
                lines.append(f"      … {len(sources) - source_limit} more sources; use JSON or the TUI Sources view.")
            if history and data["present"] and not data.get("history_available"):
                lines.append("      History not captured; use snapshot --provenance --history.")
    if limit is not None and len(changes) > limit:
        lines.append(f"  … {len(changes) - limit} more inputs; use --format json or tui.")
    return "\n".join(lines)
