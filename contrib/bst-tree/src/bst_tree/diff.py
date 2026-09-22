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


def compare(old, new):
    nodes = []
    for name in sorted(old.nodes.keys() | new.nodes.keys()):
        before, after = old.nodes.get(name), new.nodes.get(name)
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
        if before != after:
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


def render_tree(old, new, delta, show_all=False, color=False):
    marks = {"added": "+", "removed": "-", "modified": "~"}
    node_changes = {n["name"]: n for n in delta["nodes"]}
    edge_changes = {(e["from"], e["to"]): e for e in delta["edges"]}
    union = dict(old.edges)
    union.update(new.edges)
    names = old.nodes.keys() | new.nodes.keys()
    adjacency = {n: [] for n in names}
    reverse = {n: [] for n in names}
    for (parent, child), kinds in sorted(union.items()):
        adjacency[parent].append((child, kinds))
        reverse[child].append(parent)
    included = set(names) if show_all else set(node_changes)
    for parent, child in edge_changes:
        included.update((parent, child))
    queue = deque(included)
    while queue:
        for parent in reverse[queue.popleft()]:
            if parent not in included:
                included.add(parent)
                queue.append(parent)
    lines = [
        "Nodes: +{nodes_added} -{nodes_removed} ~{nodes_modified}; edges: +{edges_added} -{edges_removed} ~{edges_modified}".format(
            **delta["summary"]
        )
    ]
    for field, change in delta["metadata"].items():
        lines.append(
            f"~ {field}: {json.dumps(change['old'], ensure_ascii=False)} -> {json.dumps(change['new'], ensure_ascii=False)}"
        )
    seen = set()

    def paint(mark):
        return (
            {"+": "\033[32m+\033[0m", "-": "\033[31m-\033[0m", "~": "\033[33m~\033[0m"}.get(mark, mark)
            if color
            else mark
        )

    roots = sorted(set(old.targets) | set(new.targets))
    # Also render disconnected nodes from otherwise valid external snapshots.
    for root in roots + sorted(names - set(roots)):
        if root not in included or root in seen:
            continue
        stack = [(root, "", None, True)]
        while stack:
            name, prefix, parent, last = stack.pop()
            if name not in included:
                continue
            change = node_changes.get(name)
            edge_change = edge_changes.get((parent, name))
            mark = marks[change["change"]] if change else " "
            connector = "" if parent is None else "`-- " if last else "|-- "
            label = f"{prefix}{connector}{paint(mark)} {name}"
            if parent is not None:
                kinds = "/".join(sorted(union[parent, name]))
                if edge_change:
                    before = "/".join(edge_change["old"] or []) or "none"
                    after = "/".join(edge_change["new"] or []) or "none"
                    kinds = f"{paint(marks[edge_change['change']])} {before} -> {after}"
                label += f" [{kinds}]"
            if change and change["change"] == "modified":
                fields = [field for field in sorted(change["old"]) if change["old"][field] != change["new"][field]]
                label += (
                    " {"
                    + ", ".join(
                        f"{f}: {json.dumps(change['old'][f], ensure_ascii=False)} -> {json.dumps(change['new'][f], ensure_ascii=False)}"
                        for f in fields
                    )
                    + "}"
                )
            if name in seen:
                lines.append(label + " (see above)")
                continue
            seen.add(name)
            lines.append(label)
            children = [child for child, _ in adjacency[name] if child in included]
            child_prefix = prefix + ("" if parent is None else "    " if last else "|   ")
            for index in range(len(children) - 1, -1, -1):
                stack.append((children[index], child_prefix, name, index == len(children) - 1))
    if not has_changes(delta):
        lines.append("No changes.")
    return "\n".join(lines)
