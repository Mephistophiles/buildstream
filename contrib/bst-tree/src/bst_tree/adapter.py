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

import json
import re
import subprocess
import uuid

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .model import DETAIL_FIELDS, Graph
from bst_tools_core.project import command_base, parse_records, report_template
from bst_tools_core.process import CliRunner

FIELDS = ("name", "kind", "full-key", "source-info", "workspaced", "build-deps", "runtime-deps")


def parse_report(report, marker, *, detailed=False):
    yaml = YAML(typ="safe")

    def load_yaml(value):
        try:
            return yaml.load(value)
        except YAMLError as error:
            raise ValueError(f"Malformed YAML in bst show output: {error}") from error

    nodes, edges, details = {}, {}, {}
    fields = FIELDS + DETAIL_FIELDS if detailed else FIELDS
    for record in parse_records(report, fields, marker):
        values = {
            field: value if field in ("source-info", "build-deps", "runtime-deps") else value.strip()
            for field, value in record.items()
        }
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
        if detailed:
            details[name] = {}
            for field in DETAIL_FIELDS:
                value = load_yaml(values[field])
                if value is not None and not isinstance(value, dict):
                    raise ValueError(f"Expected mapping for {name}: {field}")
                details[name][field] = {} if value is None else value
        for field, kind in (("build-deps", "build"), ("runtime-deps", "run")):
            dependencies = load_yaml(values[field])
            if not isinstance(dependencies, list) or any(not isinstance(d, str) for d in dependencies):
                raise ValueError(f"Expected dependency list for {name}")
            for dependency in dependencies:
                edges.setdefault((name, dependency), set()).add(kind)
    result = nodes, {edge: frozenset(kinds) for edge, kinds in edges.items()}
    return (*result, details) if detailed else result


def capture(targets, directory=None, options=(), run=None, *, detailed=False, provenance=False, history=False):
    if history and not provenance:
        raise ValueError("--history requires --provenance")
    detailed = detailed or provenance
    base = command_base(directory, options)
    run = run if run is not None else CliRunner()

    def execute(arguments):
        result = run(base + arguments, stdout=subprocess.PIPE, text=True, check=False)
        if result.returncode:
            raise ValueError(f"bst failed (exit {result.returncode}); see diagnostics above")
        return result.stdout

    version = execute(["--version"]).strip()
    marker = "BST_TREE_" + uuid.uuid4().hex + "_"
    format_string = report_template(FIELDS + DETAIL_FIELDS if detailed else FIELDS, marker)
    report = execute(["show", "--deps", "all", "--order", "alpha", "--format", format_string, "--", *targets])
    parsed = parse_report(report, marker, detailed=detailed)
    nodes, edges = parsed[:2]
    # Resolve links/normalized target names using the same public interface.
    roots_report = execute(["show", "--deps", "none", "--format", "%{name}", "--", *targets])
    roots = sorted(set(line.strip() for line in roots_report.splitlines() if line.strip()))
    graph = Graph(
        roots,
        nodes,
        edges,
        {"bst_version": version, "options": dict(options), "strict": True},
        parsed[2] if detailed else None,
    )
    if provenance:
        from .provenance import validate_report

        try:
            data = validate_report(
                json.loads(
                    execute(
                        [
                            "show",
                            "--deps",
                            "all",
                            "--order",
                            "alpha",
                            "--provenance",
                            *(["--history"] if history else []),
                            "--",
                            *targets,
                        ]
                    )
                )
            )
        except ValueError as error:
            raise ValueError(
                "Snapshot provenance requires this fork's bst show --provenance. " + str(error)
            ) from error
        graph.provenance = {
            element["element"]: {key: value for key, value in element.items() if key != "resolved_inputs"}
            for element in data["elements"]
        }
        if history and any(not e.get("history_available") for e in data["elements"]):
            raise ValueError("Snapshot history requires this fork's rebuilt native node extension")
        # Reject project drift between the graph/details and provenance queries.
        repeated = execute(["show", "--deps", "all", "--order", "alpha", "--format", format_string, "--", *targets])
        if parse_report(repeated, marker, detailed=True) != parsed:
            raise ValueError("Project changed during provenance capture; retry the snapshot")
        if any(not isinstance(e.get("resolved_inputs"), dict) for e in data["elements"]):
            raise ValueError("Snapshot provenance requires this fork's lossless resolved_inputs JSON export")
        # Public show YAML is a display format and may change scalar types or
        # trailing newlines. Use exact resolved values from the provenance load.
        graph.details = {e["element"]: e["resolved_inputs"] for e in data["elements"]}
    return Graph.from_dict(graph.as_dict())
