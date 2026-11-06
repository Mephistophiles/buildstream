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
"""Structural rebuild candidates restricted to a target-to-change corridor."""

from collections import deque
from dataclasses import replace
import heapq
import json
import shlex
import sys


def reachable(adjacency, start):
    if start not in adjacency:
        return set()
    visited = {start}
    queue = deque([start])
    while queue:
        for child, _ in adjacency[queue.popleft()]:
            if child not in visited:
                visited.add(child)
                queue.append(child)
    return visited


def rebuild_report(graph, target, changed):
    adjacency = graph.adjacency()
    reverse = graph.adjacency(reverse=True)
    names = reachable(adjacency, target) & reachable(reverse, changed)
    # Kahn's algorithm, reversed: dependencies precede their consumers.
    pending = {name: sum(child in names for child, _ in adjacency[name]) for name in names}
    ready = [name for name in names if pending[name] == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        name = heapq.heappop(ready)
        ordered.append(name)
        for parent, _ in reverse[name]:
            if parent in pending:
                pending[parent] -= 1
                if pending[parent] == 0:
                    heapq.heappush(ready, parent)
    if len(ordered) != len(names):
        raise ValueError("Dependency cycle in rebuild selection; cannot order dependencies before consumers")
    return {
        "target": target,
        "changed": changed,
        "elements": ordered,
        "edges": [
            {"from": a, "to": b, "types": sorted(types)}
            for (a, b), types in sorted(graph.edges.items())
            if a in names and b in names
        ],
        "selection": "all-paths",
        "cache_checked": False,
    }


def render_rebuild(report, graph, args):
    if args.format == "chains":
        from .headless import dependency_chains

        view = replace(graph, targets=[args.source]).scoped(args.scope)
        paths, truncated = dependency_chains(view, args.source, args.element, args.max_chains)
        for path in paths:
            print(" -> ".join(path))
        if truncated:
            print("bst-tree: chain limit reached; increase --max-chains for more results", file=sys.stderr)
            return 3
    elif args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    elif report["elements"]:
        print(shlex.join(report["elements"]))
    return 0 if report["elements"] else 1
