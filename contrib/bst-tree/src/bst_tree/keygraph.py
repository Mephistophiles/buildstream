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
"""Compact strict build-input reachability, including cyclic runtime graphs."""

from collections import deque


class KeyGraph:
    """Bitsets avoid enumerating every transitive path or storing quadratic string lists."""

    def __init__(self, graph, names):
        self.names = names
        index = {name: i for i, name in enumerate(names)}
        count = len(names)
        runtime = [[] for _ in names]
        reverse = [[] for _ in names]
        build = [[] for _ in names]
        for (parent, child), kinds in graph.edges.items():
            p, c = index[parent], index[child]
            if "run" in kinds:
                runtime[p].append(c)
                reverse[c].append(p)
            if "build" in kinds:
                build[p].append(c)
        # Iterative Kosaraju: snapshots may contain cycles and deep chains.
        visited, finished = set(), []
        for root in range(count):
            if root in visited:
                continue
            visited.add(root)
            stack = [(root, iter(runtime[root]))]
            while stack:
                node, children = stack[-1]
                child = next(children, None)
                if child is None:
                    finished.append(node)
                    stack.pop()
                elif child not in visited:
                    visited.add(child)
                    stack.append((child, iter(runtime[child])))
        component = [-1] * count
        groups = []
        for root in reversed(finished):
            if component[root] != -1:
                continue
            number = len(groups)
            members = []
            component[root] = number
            pending = [root]
            while pending:
                node = pending.pop()
                members.append(node)
                for parent in reverse[node]:
                    if component[parent] == -1:
                        component[parent] = number
                        pending.append(parent)
            groups.append(members)
        children = [set() for _ in groups]
        incoming = [0] * len(groups)
        for parent, edges in enumerate(runtime):
            for child in edges:
                p, c = component[parent], component[child]
                if p != c and c not in children[p]:
                    children[p].add(c)
                    incoming[c] += 1
        pending = deque(c for c, degree in enumerate(incoming) if degree == 0)
        order = []
        while pending:
            parent = pending.popleft()
            order.append(parent)
            for child in children[parent]:
                incoming[child] -= 1
                if incoming[child] == 0:
                    pending.append(child)
        closure = [sum(1 << node for node in members) for members in groups]
        for parent in reversed(order):
            for child in children[parent]:
                closure[parent] |= closure[child]
        self.runtime = [closure[component[node]] & ~(1 << node) for node in range(count)]
        self.inputs = [0] * count
        self.build_runtime_root = [False] * count
        consumers = [0] * len(groups)
        for parent, dependencies in enumerate(build):
            for child in dependencies:
                self.inputs[parent] |= closure[component[child]]
                consumers[component[child]] |= 1 << parent
            self.build_runtime_root[parent] = bool(self.inputs[parent] & (1 << parent))
            self.inputs[parent] &= ~(1 << parent)
        # Runtime descendants inherit the build consumers of their ancestors.
        for parent in order:
            for child in children[parent]:
                consumers[child] |= consumers[parent]
        self.consumers = [consumers[component[node]] & ~(1 << node) for node in range(count)]


def members(mask, names, limit=None):
    result = []
    while mask and (limit is None or len(result) < limit):
        bit = mask & -mask
        result.append(names[bit.bit_length() - 1])
        mask ^= bit
    return result
