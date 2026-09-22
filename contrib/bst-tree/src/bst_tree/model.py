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
"""Portable graph model and versioned snapshots (no BuildStream imports)."""

from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile


@dataclass
class Graph:
    targets: list[str]
    nodes: dict[str, dict]
    edges: dict[tuple[str, str], frozenset[str]]
    metadata: dict

    def adjacency(self, reverse=False):
        result = {name: [] for name in self.nodes}
        for (parent, child), kinds in sorted(self.edges.items()):
            if reverse:
                parent, child = child, parent
            result[parent].append((child, kinds))
        return result

    def scoped(self, scope):
        if scope == "all":
            return self
        adjacency = self.adjacency()
        edges = {}
        included = set(self.targets)
        queue = deque()
        for root in self.targets:
            for child, kinds in adjacency[root]:
                if ("build" if scope == "build" else "run") in kinds:
                    edges[root, child] = kinds
                    included.add(child)
                    queue.append(child)
        visited = set()
        while queue:
            parent = queue.popleft()
            if parent in visited:
                continue
            visited.add(parent)
            for child, kinds in adjacency[parent]:
                if "run" in kinds:
                    edges[parent, child] = kinds
                    included.add(child)
                    queue.append(child)
        return Graph(self.targets, {n: self.nodes[n] for n in included}, edges, self.metadata)

    def paths_to(self, destination):
        """One shortest path per root; linear traversal rather than path enumeration."""
        adjacency = self.adjacency()
        paths = []
        for root in self.targets:
            parents = {root: None}
            queue = deque([root])
            while queue and destination not in parents:
                parent = queue.popleft()
                for child, _ in adjacency[parent]:
                    if child not in parents:
                        parents[child] = parent
                        queue.append(child)
            if destination in parents:
                path = []
                current = destination
                while current is not None:
                    path.append(current)
                    current = parents[current]
                paths.append(list(reversed(path)))
        return paths

    def as_dict(self):
        return {
            "format_version": 1,
            "targets": sorted(set(self.targets)),
            "metadata": self.metadata,
            "nodes": {name: self.nodes[name] for name in sorted(self.nodes)},
            "edges": [
                {"from": parent, "to": child, "types": sorted(kinds)}
                for (parent, child), kinds in sorted(self.edges.items())
            ],
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or type(data.get("format_version")) is not int or data["format_version"] != 1:
            raise ValueError("Unsupported snapshot format (expected version 1)")
        targets, nodes, records = data.get("targets"), data.get("nodes"), data.get("edges")
        if not isinstance(nodes, dict) or not nodes or not all(isinstance(n, str) and n for n in nodes):
            raise ValueError("Snapshot requires named nodes")
        if (
            not isinstance(targets, list)
            or not targets
            or any(not isinstance(t, str) or t not in nodes for t in targets)
        ):
            raise ValueError("Snapshot targets must refer to existing nodes")
        fields = {"kind", "key", "source_info", "workspace"}
        for node in nodes.values():
            if not isinstance(node, dict) or set(node) != fields:
                raise ValueError("Invalid snapshot node fields")
            if not isinstance(node["kind"], str) or not isinstance(node["workspace"], bool):
                raise ValueError("Invalid node kind or workspace")
            if node["key"] is not None and not isinstance(node["key"], str):
                raise ValueError("Invalid node key")
            if node["source_info"] is not None and not isinstance(node["source_info"], list):
                raise ValueError("Invalid source information")
        if not isinstance(records, list) or not isinstance(data.get("metadata"), dict):
            raise ValueError("Invalid snapshot edges or metadata")
        edges = {}
        for edge in records:
            if not isinstance(edge, dict) or set(edge) != {"from", "to", "types"}:
                raise ValueError("Invalid edge")
            parent, child, kinds = edge["from"], edge["to"], edge["types"]
            if not isinstance(parent, str) or not isinstance(child, str) or parent not in nodes or child not in nodes:
                raise ValueError("Edge refers to missing node")
            if not isinstance(kinds, list) or not kinds or any(k not in ("build", "run") for k in kinds):
                raise ValueError("Invalid edge types")
            if (parent, child) in edges:
                raise ValueError("Duplicate edge")
            edges[parent, child] = frozenset(kinds)
        return cls(sorted(set(targets)), nodes, edges, data["metadata"])


def read_snapshot(path):
    with open(path, encoding="utf-8") as stream:
        return Graph.from_dict(json.load(stream))


def write_snapshot(graph, path):
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(graph.as_dict(), stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
