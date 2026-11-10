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
"""Structural comparison and text rendering for dependency graphs."""

from collections import Counter, defaultdict, deque
import json

from .input_diff import changed_paths
from .provenance import lookup

NODE_FIELDS = ("kind", "key", "source_info", "workspace")
DIFF_FIELDS = (*NODE_FIELDS, "metadata", "details")
MARKS = {"added": "+", "removed": "-", "modified": "~"}


def compare(old, new, fields=None):
    """Compare selected attributes, always retaining node/edge/target changes."""
    fields = set(DIFF_FIELDS if fields is None else fields)
    unknown = fields - set(DIFF_FIELDS)
    if unknown:
        raise ValueError("Unknown diff fields: " + ", ".join(sorted(unknown)))

    def selected(graph, name):
        node = graph.nodes.get(name)
        if node is None:
            return None
        result = {field: value for field, value in node.items() if field in fields}
        if "details" in fields and old.details is not None and new.details is not None:
            result["details"] = graph.details[name]
        return result

    nodes = []
    for name in sorted(old.nodes.keys() | new.nodes.keys()):
        before, after = selected(old, name), selected(new, name)
        if before != after:
            nodes.append(
                {
                    "name": name,
                    "change": "added" if before is None else "removed" if after is None else "modified",
                    "old": before,
                    "new": after,
                }
            )
    edges = []
    for parent, child in sorted(old.edges.keys() | new.edges.keys()):
        before, after = old.edges.get((parent, child)), new.edges.get((parent, child))
        if before != after:
            edges.append(
                {
                    "from": parent,
                    "to": child,
                    "change": "added" if before is None else "removed" if after is None else "modified",
                    "old": sorted(before) if before else None,
                    "new": sorted(after) if after else None,
                }
            )
    metadata = {}
    for field, before, after in (("targets", old.targets, new.targets), ("metadata", old.metadata, new.metadata)):
        if (field == "targets" or field in fields) and before != after:
            metadata[field] = {"old": before, "new": after}
    summary = {
        f"{category}_{kind}": sum(item["change"] == kind for item in items)
        for category, items in (("nodes", nodes), ("edges", edges))
        for kind in ("added", "removed", "modified")
    }
    return {
        "format_version": 1,
        "snapshot_versions": {"old": old.format_version, "new": new.format_version},
        "summary": summary,
        "nodes": nodes,
        "edges": edges,
        "metadata": metadata,
    }


def has_changes(delta):
    return bool(delta["nodes"] or delta["edges"] or delta["metadata"])


def _paint(text, mark, color):
    code = {"+": "32", "-": "31", "~": "33"}.get(mark)
    return f"\033[{code}m{text}\033[0m" if color and code else text


def render_flat(old, new, delta, show_all=False, color=False):
    """List each changed element once and share identical parameter transitions."""
    statuses = {node["name"]: node["change"] for node in delta["nodes"]}
    dependencies = defaultdict(list)
    for edge in delta["edges"]:
        statuses.setdefault(edge["from"], "modified")
        mark = MARKS[edge["change"]]
        before, after = ("/".join(edge[side] or []) for side in ("old", "new"))
        roles = f"{before} -> {after}" if before and after else before or after
        dependencies[edge["from"]].append(_paint(f"{mark} {edge['to']} [{roles}]", mark, color))

    affected = defaultdict(list)
    for node in delta["nodes"]:
        if node["change"] != "modified":
            continue
        before, after = node["old"], node["new"]
        for path in changed_paths(before, after):
            values = []
            for side in (before, after):
                present, value = lookup(side, path)
                values.append(json.dumps(value, ensure_ascii=False, sort_keys=True) if present else "[absent]")
            affected[(path, *values)].append(node["name"])

    def describe(transition):
        path, before, after = transition
        label = path.removeprefix("/details/").lstrip("/")
        return f"{label}: " + _paint(before, "-", color) + " -> " + _paint(after, "+", color)

    shared = []
    parameters = defaultdict(list)
    for transition, names in affected.items():
        note = describe(transition)
        if len(names) > 1:
            label = f"@{len(shared) + 1}"
            shared.append(f"  {label} {note}")
            note = label
        for name in names:
            parameters[name].append((transition[0], note))
    counts = Counter(statuses.values())
    summary = delta["summary"]
    lines = [
        f"Packages: +{counts['added']} -{counts['removed']} ~{counts['modified']} | "
        f"edges: +{summary['edges_added']} -{summary['edges_removed']} ~{summary['edges_modified']}"
    ]
    for field, change in delta["metadata"].items():
        values = [json.dumps(change[side], ensure_ascii=False, sort_keys=True) for side in ("old", "new")]
        lines.append(_paint("~", "~", color) + " " + describe((field, *values)))

    names = old.nodes.keys() | new.nodes.keys() if show_all else statuses
    for name in sorted(names):
        mark = MARKS.get(statuses.get(name), " ")
        parts = [_paint(f"{mark} {name}", mark, color)]
        if name in dependencies:
            parts.append("deps: " + ", ".join(dependencies[name]))
        parts.extend(note for _, note in sorted(parameters.get(name, ())))
        lines.append(" | ".join(parts))
    if shared:
        lines.append("\nShared parameter changes:")
        lines.extend(shared)
    if not has_changes(delta):
        lines.append("No changes.")
    return "\n".join(lines)


def render_tree(old, new, delta, show_all=False, color=False, reverse=False):
    """Render changed edges and context paths without unrelated shared branches."""
    node_changes = {n["name"]: n for n in delta["nodes"]}
    edge_changes = {(e["from"], e["to"]): e for e in delta["edges"]}
    union = {**old.edges, **new.edges}
    names = old.nodes.keys() | new.nodes.keys()
    parents = {n: [] for n in names}
    for parent, child in sorted(union):
        parents[child].append(parent)

    # An unchanged incoming edge to the child of a changed edge is NOT itself
    # context. Start at the changed edge's owner, not both endpoints.
    seeds = set(node_changes) | {parent for parent, _ in edge_changes}
    context = set(seeds)
    queue = deque(sorted(seeds))
    visible_edges = set(union) if show_all else set(edge_changes)
    while queue:
        child = queue.popleft()
        for parent in parents[child]:
            visible_edges.add((parent, child))
            if parent not in context:
                context.add(parent)
                queue.append(parent)
    included = set(names) if show_all else context | {child for _, child in edge_changes}
    adjacency = {n: [] for n in included}
    incoming = set()
    for parent, child in sorted(visible_edges):
        if reverse:
            parent, child = child, parent
        adjacency[parent].append(child)
        incoming.add(child)

    lines = [
        "Nodes: +{nodes_added} -{nodes_removed} ~{nodes_modified}; edges: +{edges_added} -{edges_removed} ~{edges_modified}".format(
            **delta["summary"]
        )
    ]
    if reverse:
        lines.append("Reverse dependencies (dependency -> consumers):")
    for field, change in delta["metadata"].items():
        lines.append(
            _paint(
                f"~ {field}: {json.dumps(change['old'], ensure_ascii=False)} -> {json.dumps(change['new'], ensure_ascii=False)}",
                "~",
                color,
            )
        )

    # Expand each node once, but repeat its local changes at every occurrence.
    # Explicit references identify the element whose dependency changes to read;
    # leaves need no reference at all. This keeps diamonds and cycles bounded.
    expanded = set()
    locations = {}
    roots = sorted(included - incoming)
    if not reverse:
        roots = sorted((set(old.targets) | set(new.targets)) & included) + roots
    for root in roots + sorted(included):
        if root in expanded:
            continue
        stack = [(root, "", None, True)]
        while stack:
            name, prefix, parent, last = stack.pop()
            change = node_changes.get(name)
            edge = (name, parent) if reverse else (parent, name)
            edge_change = edge_changes.get(edge)
            mark = MARKS[change["change"]] if change else " "
            connector = "" if parent is None else "`-- " if last else "|-- "
            label = _paint(f"{mark} {name}", mark, color)
            if parent is not None:
                kinds = "/".join(sorted(union[edge]))
                if edge_change:
                    before = "/".join(edge_change["old"] or []) or "none"
                    after = "/".join(edge_change["new"] or []) or "none"
                    edge_mark = MARKS[edge_change["change"]]
                    kinds = _paint(f"{edge_mark} {before} -> {after}", edge_mark, color)
                label += f" [{kinds}]"
            if change and change["change"] == "modified":
                fields = [field for field in sorted(change["old"]) if change["old"][field] != change["new"][field]]
                label += (
                    " {"
                    + ", ".join(
                        f"{f}: "
                        + _paint(json.dumps(change["old"][f], ensure_ascii=False), "-", color)
                        + " -> "
                        + _paint(json.dumps(change["new"][f], ensure_ascii=False), "+", color)
                        for f in fields
                    )
                    + "}"
                )
            children = adjacency[name]
            if name in expanded and children:
                label += f" (shared subtree: {name}; expanded at line {locations[name]})"
            lines.append(prefix + connector + label)
            if name in expanded:
                continue
            expanded.add(name)
            locations[name] = len(lines)
            child_prefix = prefix + ("" if parent is None else "    " if last else "|   ")
            for index in range(len(children) - 1, -1, -1):
                stack.append((children[index], child_prefix, name, index == len(children) - 1))
    if not has_changes(delta):
        lines.append("No changes.")
    return "\n".join(lines)
