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
"""Serialize winning composed YAML locations and variable references without guessing history."""

import re
from .node import MappingNode, SequenceNode

VARIABLE = re.compile(r"%\{([a-zA-Z][a-zA-Z0-9_-]*)\}")


def pointer(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def build_report(name, raw_nodes, resolved):
    entries = []

    def walk(node, parts):
        if isinstance(node, MappingNode) and node:
            for key, child in sorted(node.items()):
                walk(child, parts + [key])
            return
        if isinstance(node, SequenceNode) and node:
            for index, child in enumerate(node):
                walk(child, parts + [index])
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
        origin = node.get_provenance()
        references = sorted(set(VARIABLE.findall(raw))) if isinstance(raw, str) else []
        entries.append(
            {
                "path": "/" + "/".join(pointer(p) for p in parts),
                "raw": raw,
                "value": value,
                "value_available": available,
                "origin": {
                    "file": None if origin._is_synthetic else origin._filename,
                    "display": str(origin),
                    "line": None if origin._is_synthetic else origin._line,
                    "column": None if origin._is_synthetic else origin._col + 1,
                    "synthetic": bool(origin._is_synthetic),
                },
                "references": [{"variable": ref, "path": "/vars/" + pointer(ref)} for ref in references],
            }
        )

    for section, node in sorted(raw_nodes.items()):
        walk(node, [section])
    return {
        "element": name,
        "entries": entries,
        "coverage": "winning-composed-inputs",
        "notes": [
            "Locations describe the winning composed YAML nodes before variable expansion.",
            "Overwritten definitions, inactive conditional branches and a complete override history are not captured.",
            "Variable references describe expansion links, not compiler execution or plugin-internal configuration transformations.",
            "Synthetic/generated values have no source file location.",
        ],
    }
