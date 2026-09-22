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
"""Read the documented bst show interface, with framed multiline fields."""

import re
import subprocess
import uuid

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .model import Graph

FIELDS = ("name", "kind", "full-key", "source-info", "workspaced", "build-deps", "runtime-deps")


def parse_report(report, marker):
    yaml = YAML(typ="safe")

    def load_yaml(value):
        try:
            return yaml.load(value)
        except YAMLError as error:
            raise ValueError(f"Malformed YAML in bst show output: {error}") from error

    nodes, edges = {}, {}
    sections = report.split(marker + "record\n")
    if sections[0].strip():
        raise ValueError("Unexpected output before bst show records")
    for section in sections[1:]:
        values = {}
        for index, field in enumerate(FIELDS):
            prefix = marker + field + "\n"
            if not section.startswith(prefix):
                raise ValueError(f"Malformed bst show field: {field}")
            section = section[len(prefix) :]
            following = marker + (FIELDS[index + 1] if index + 1 < len(FIELDS) else "end") + "\n"
            value, separator, remainder = section.partition(following)
            if not separator or marker in value:
                raise ValueError(f"Missing bst show boundary for {field}")
            values[field] = value if field in ("source-info", "build-deps", "runtime-deps") else value.strip()
            section = following + remainder
        if section != marker + "end\n" and section.strip() != marker + "end":
            raise ValueError("Unexpected trailing bst show output")
        name = values["name"]
        if not name or name in nodes or any(v.strip() == "%{" + f + "}" for f, v in values.items()):
            raise ValueError("Duplicate node or unsupported bst show format field")
        source_info = load_yaml(values["source-info"])
        if not isinstance(source_info, list):
            raise ValueError("Expected source-info list from bst show")
        key = values["full-key"]
        nodes[name] = {
            "kind": values["kind"],
            "key": key if re.fullmatch(r"[0-9a-f]{64}", key) else None,
            # Empty provenance cannot distinguish no sources from unsupported plugins.
            "source_info": source_info or None,
            "workspace": bool(values["workspaced"]),
        }
        for field, kind in (("build-deps", "build"), ("runtime-deps", "run")):
            dependencies = load_yaml(values[field])
            if not isinstance(dependencies, list) or any(not isinstance(d, str) for d in dependencies):
                raise ValueError(f"Expected dependency list for {name}")
            for dependency in dependencies:
                edges.setdefault((name, dependency), set()).add(kind)
    return nodes, {edge: frozenset(kinds) for edge, kinds in edges.items()}


def capture(targets, directory=None, options=(), run=subprocess.run):
    base = ["bst", "--no-colors", "--strict"]
    if directory:
        base += ["-C", str(directory)]
    for name, value in options:
        base += ["--option", name, value]

    def execute(arguments):
        result = run(base + arguments, stdout=subprocess.PIPE, text=True, check=False)
        if result.returncode:
            raise ValueError(f"bst failed (exit {result.returncode}); see diagnostics above")
        return result.stdout

    version = execute(["--version"]).strip()
    marker = "BST_TREE_" + uuid.uuid4().hex + "_"
    format_string = marker + "record\n"
    for field in FIELDS:
        format_string += marker + field + "\n%{" + field + "}\n"
    format_string += marker + "end\n"
    report = execute(["show", "--deps", "all", "--order", "alpha", "--format", format_string, "--", *targets])
    nodes, edges = parse_report(report, marker)
    # Resolve links/normalized target names using the same public interface.
    roots_report = execute(["show", "--deps", "none", "--format", "%{name}", "--", *targets])
    roots = sorted(set(line.strip() for line in roots_report.splitlines() if line.strip()))
    graph = Graph(roots, nodes, edges, {"bst_version": version, "options": dict(options), "strict": True})
    return Graph.from_dict(graph.as_dict())
