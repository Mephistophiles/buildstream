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
"""Serialize winning YAML locations, references and explicitly captured composition history."""

import re
from .node import MappingNode, SequenceNode

VARIABLE = re.compile(r"%\{([a-zA-Z][a-zA-Z0-9_-]*)\}")


def pointer(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def location(node, *, generated=False):
    origin = node.get_provenance()
    synthetic = generated or origin._is_synthetic
    return {
        "file": None if synthetic else origin._filename,
        "display": "synthetic/generated" if generated else str(origin),
        "line": None if synthetic else origin._line,
        "column": None if synthetic else origin._col + 1,
        "synthetic": bool(synthetic),
    }


def build_report(name, raw_nodes, resolved, *, history=None):
    entries = []

    def walk(node, parts):
        if isinstance(node, MappingNode) and node:
            for key, child in sorted(node.items()):
                walk(child, parts + [key])
            return
        if isinstance(node, SequenceNode) and node:
            for index, child in enumerate(node):
                walk(child, parts + [index])
            if history is None:
                return
        raw = node.strip_node_info()
        value = resolved
        available = True
        for part in parts:
            try:
                value = value[part]
            except (KeyError, IndexError, TypeError):
                available = False
                value = None
                break
        references = sorted(set(VARIABLE.findall(raw))) if isinstance(raw, str) else []
        entries.append(
            {
                "path": "/" + "/".join(pointer(p) for p in parts),
                "raw": raw,
                "value": value,
                "value_available": available,
                "origin": history.origin(node) if history is not None else location(node),
                "references": [{"variable": ref, "path": "/vars/" + pointer(ref)} for ref in references],
            }
        )
        if history is not None:
            steps = history.snapshot_history(node)
            last_reset = 0
            for index, step in enumerate(steps):
                if step["operation"] != "compose-list" or (isinstance(step["raw"], dict) and "(=)" in step["raw"]):
                    last_reset = index
            if isinstance(node, SequenceNode):
                states = ["overridden" if i < last_reset else "contributes" for i in range(len(steps))]
                if steps[-1]["raw"] == raw and steps[-1]["operation"] != "compose-list":
                    states[-1] = "effective"
            else:
                states = ["overridden"] * (len(steps) - 1) + ["effective"]
            entries[-1]["history"] = [dict(step, state=state) for step, state in zip(steps, states)]

    for section, node in sorted(raw_nodes.items()):
        walk(node, [section])
    return {
        "element": name,
        "entries": entries,
        "coverage": "applied-yaml-composition" if history is not None else "winning-composed-inputs",
        "history_available": history is not None,
        "notes": [
            "Locations describe the winning composed YAML nodes before variable expansion.",
            (
                "History records applied definitions, list directives and generated scalar assignments; inactive branches are not applied."
                if history is not None
                else "Use --history to capture applied overwritten definitions and list composition."
            ),
            "Variable references describe expansion links, not compiler execution or plugin-internal configuration transformations.",
            "History is definition precedence, not build execution; direct plugin/internal mutations outside the observed operations are not reconstructed.",
            "Synthetic/generated values have no source file location.",
        ],
    }
