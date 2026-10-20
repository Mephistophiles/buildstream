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
"""Query-scoped definition lineage observed during real YAML composition."""

from contextlib import contextmanager
from . import node as nodes
from ._input_provenance import location


class CompositionHistory:
    def __init__(self):
        # Strong keys prevent node-address reuse while this query is active.
        self.lineage = {}
        self.conditions = {}
        self.generated_nodes = set()

    def definition(self, node, operation="define"):
        result = {
            "raw": node.strip_node_info(),
            "origin": location(node, generated=node in self.generated_nodes),
            "operation": operation,
        }
        if node in self.conditions:
            result["conditions"] = self.conditions[node]
        return result

    def snapshot_history(self, node):
        steps = self.lineage.get(node)
        return list(steps) if steps is not None else [self.definition(node)]

    @staticmethod
    def merge(previous, incoming):
        # Composing already-composed defaults can repeat a common suffix/prefix.
        # Preserve definition order without multiplying identical lineage.
        overlap = 0
        for length in range(1, min(len(previous), len(incoming)) + 1):
            if previous[-length:] == incoming[:length]:
                overlap = length
        return previous + incoming[overlap:]

    def cloned(self, source, result):
        if source in self.lineage:
            self.lineage[result] = self.lineage[source]
        if source in self.conditions:
            self.conditions[result] = self.conditions[source]
        if source in self.generated_nodes:
            self.generated_nodes.add(result)

    def replaced(self, source, target, result):
        previous = self.snapshot_history(target) if target is not None else []
        incoming = self.snapshot_history(source)
        self.lineage[result] = self.merge(previous, incoming)

    def generated(self, target, result):
        self.generated_nodes.add(result)
        previous = self.snapshot_history(target) if target is not None else []
        self.lineage[result] = self.merge(previous, [self.definition(result, "generated")])

    def list_composed(self, source, result, previous):
        incoming = [dict(step, operation="compose-list") for step in self.snapshot_history(source)]
        self.lineage[result] = self.merge(previous, incoming)

    def condition(self, node, expression, condition_node):
        condition = {"expression": expression, "origin": location(condition_node)}
        pending = [node]
        while pending:
            current = pending.pop()
            self.conditions[current] = [*self.conditions.get(current, ()), condition]
            # Add the applied condition to any precomposed definitions too.
            if current in self.lineage:
                self.lineage[current] = [
                    dict(step, conditions=[*step.get("conditions", ()), condition]) for step in self.lineage[current]
                ]
            if isinstance(current, nodes.MappingNode):
                pending.extend(current.values())
            elif isinstance(current, nodes.SequenceNode):
                pending.extend(current)

    def origin(self, node):
        return location(node, generated=node in self.generated_nodes)

    def clear(self):
        self.lineage.clear()
        self.conditions.clear()
        self.generated_nodes.clear()


@contextmanager
def capture_history(enabled):
    if not enabled:
        yield None
        return
    history = CompositionHistory()
    previous = nodes._set_composition_observer(history)
    try:
        yield history
    finally:
        nodes._set_composition_observer(previous)
        history.clear()
