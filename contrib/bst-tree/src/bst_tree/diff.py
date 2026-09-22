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
"""Structural comparison and bounded tree rendering for DAGs."""

from collections import deque
import json

NODE_FIELDS = ("kind", "key", "source_info", "workspace")
DIFF_FIELDS = (*NODE_FIELDS, "metadata")


def compare(old, new, fields=None):
    """Compare selected attributes, always retaining node/edge/target changes."""
    fields = set(DIFF_FIELDS if fields is None else fields)
    unknown = fields - set(DIFF_FIELDS)
    if unknown:
        raise ValueError("Unknown diff fields: " + ", ".join(sorted(unknown)))

    def selected(node):
        return {field: value for field, value in node.items() if field in fields} if node is not None else None

    nodes = []
    for name in sorted(old.nodes.keys() | new.nodes.keys()):
        before, after = selected(old.nodes.get(name)), selected(new.nodes.get(name))
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
        "snapshot_versions": {"old": 1, "new": 1},
        "summary": summary,
        "nodes": nodes,
        "edges": edges,
        "metadata": metadata,
    }


def has_changes(delta):
    return bool(delta["nodes"] or delta["edges"] or delta["metadata"])


def render_tree(old, new, delta, show_all=False, color=False, reverse=False):
    """Render changed edges and context paths without unrelated shared branches."""
    marks = {"added": "+", "removed": "-", "modified": "~"}
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

    def paint(text, mark):
        code = {"+": "32", "-": "31", "~": "33"}.get(mark)
        return f"\033[{code}m{text}\033[0m" if color and code else text

    lines = [
        "Nodes: +{nodes_added} -{nodes_removed} ~{nodes_modified}; edges: +{edges_added} -{edges_removed} ~{edges_modified}".format(
            **delta["summary"]
        )
    ]
    if reverse:
        lines.append("Reverse dependencies (dependency -> consumers):")
    for field, change in delta["metadata"].items():
        lines.append(
            paint(
                f"~ {field}: {json.dumps(change['old'], ensure_ascii=False)} -> {json.dumps(change['new'], ensure_ascii=False)}",
                "~",
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
            mark = marks[change["change"]] if change else " "
            connector = "" if parent is None else "`-- " if last else "|-- "
            label = paint(f"{mark} {name}", mark)
            if parent is not None:
                kinds = "/".join(sorted(union[edge]))
                if edge_change:
                    before = "/".join(edge_change["old"] or []) or "none"
                    after = "/".join(edge_change["new"] or []) or "none"
                    edge_mark = marks[edge_change["change"]]
                    kinds = paint(f"{edge_mark} {before} -> {after}", edge_mark)
                label += f" [{kinds}]"
            if change and change["change"] == "modified":
                fields = [field for field in sorted(change["old"]) if change["old"][field] != change["new"][field]]
                label += (
                    " {"
                    + ", ".join(
                        f"{f}: "
                        + paint(json.dumps(change["old"][f], ensure_ascii=False), "-")
                        + " -> "
                        + paint(json.dumps(change["new"][f], ensure_ascii=False), "+")
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
