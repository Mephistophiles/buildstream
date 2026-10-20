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
"""Query exported winning input provenance, including transitive variable references."""

import json
import re
from bst_tools_core.project import command_base
from bst_tools_core.process import CliRunner
from .headless import project_arguments
from .provenance import validate_report


def configure_parser(commands):
    p = commands.add_parser(
        "origin",
        help="Explain where effective config/variables/environment values came from",
        description="Requires this fork’s bst show --provenance, or a saved provenance JSON report.",
    )
    p.add_argument("element", nargs="?", help="Element, including junction qualification")
    p.add_argument(
        "--from-report", metavar="FILE", help="Read a saved bst show --provenance JSON document without bst"
    )
    p.add_argument(
        "--path",
        action="append",
        default=[],
        help="RFC 6901 pointer/prefix, e.g. /env/CFLAGS or /config/build-commands",
    )
    p.add_argument("--no-follow", action="store_true", help="Omit transitive variable-reference evidence")
    p.add_argument(
        "--history",
        action="store_true",
        help="Show applied YAML overrides, list composition and conditions; requires rebuilt fork bst",
    )
    project_arguments(p)
    p.set_defaults(command="origin")
    return p


def select(report, element=None, paths=(), follow=True, history=False):
    for path in paths:
        if not path.startswith("/") or re.search(r"~(?![01])", path):
            raise ValueError("Expected an RFC 6901 pointer starting with /")
    elements = [e for e in report["elements"] if element is None or e["element"] == element]
    if not elements:
        raise ValueError("Element is absent from provenance report")
    result = {
        "format_version": 1,
        "query": "input-provenance",
        "requested_paths": list(paths),
        "follow_references": follow,
        "elements": [],
    }
    matched = set()
    for data in elements:
        if history and not data.get("history_available"):
            raise ValueError("History unavailable in this report; capture it with bst show --provenance --history")
        by_path = {entry["path"]: entry for entry in data["entries"]}
        selected = set()
        for query in paths:
            matches = {p for p in by_path if query == "/" or p == query or p.startswith(query.rstrip("/") + "/")}
            if matches:
                matched.add(query)
            selected.update(matches)
        if not paths:
            selected = set(by_path)
        requested = set(selected)
        pending = list(sorted(selected))
        unavailable = []
        while pending and follow:
            path = pending.pop()
            for reference in by_path[path]["references"]:
                ref = reference["path"]
                if ref not in by_path:
                    unavailable.append(reference)
                elif ref not in selected:
                    selected.add(ref)
                    pending.append(ref)
        if history and any("history" not in by_path[p] for p in selected):
            raise ValueError("Selected entries lack captured history; use bst show --provenance --history")
        result["elements"].append(
            {
                **{key: value for key, value in data.items() if key != "resolved_inputs"},
                "entries": [
                    dict({k: v for k, v in by_path[p].items() if history or k != "history"}, selected=p in requested)
                    for p in sorted(selected)
                ],
                "unavailable_references": unavailable,
            }
        )
    unknown = set(paths) - matched
    if unknown:
        raise ValueError("Paths absent from provenance report: " + ", ".join(sorted(unknown)))
    return result


def render_origin(report):
    lines = []
    for element in report["elements"]:
        lines.append(element["element"])
        for entry in element["entries"]:
            origin = entry["origin"]
            where = (
                "synthetic/generated"
                if origin.get("synthetic")
                else str(origin.get("file")) + ":" + str(origin.get("line")) + ":" + str(origin.get("column"))
            )
            lines += [
                "  " + entry["path"] + ("" if entry["selected"] else " [variable evidence]"),
                "    value: " + json.dumps(entry.get("value"), ensure_ascii=True),
                "    raw: " + json.dumps(entry.get("raw"), ensure_ascii=True),
                "    origin: " + where,
            ]
            if not entry["value_available"]:
                lines.append("    resolved value unavailable")
            if entry["references"]:
                lines.append("    references: " + ", ".join(r["path"] for r in entry["references"]))
            steps = entry.get("history", [])
            if len(steps) > 1 or any(step.get("conditions") for step in steps):
                lines.append("    history:")
                for step in steps:
                    source = step["origin"]
                    where = (
                        "synthetic/generated"
                        if source.get("synthetic")
                        else f"{source.get('file')}:{source.get('line')}:{source.get('column')}"
                    )
                    conditions = ", ".join(c["expression"] for c in step.get("conditions", []))
                    label = step["state"] + ("; " + step["operation"] if step["operation"] != "define" else "")
                    lines.append(
                        "      "
                        + label
                        + ": "
                        + json.dumps(step["raw"], ensure_ascii=True)
                        + " @ "
                        + where
                        + (" [if " + conditions + "]" if conditions else "")
                    )
        for note in element.get("notes", []):
            lines.append("  " + note)
        if element["unavailable_references"]:
            lines.append("  Some referenced variable evidence is unavailable.")
    return "\n".join(lines)


def run_origin(args):
    if args.from_report and (args.directory or args.option):
        raise ValueError("--from-report cannot be combined with project arguments")
    if not args.from_report and not args.element:
        raise ValueError("origin requires an element or --from-report")
    runner = CliRunner(args.timeout, args.output_limit)
    try:
        if args.from_report:
            with open(args.from_report, encoding="utf-8") as stream:
                report = json.load(stream)
        else:
            try:
                text = runner(
                    command_base(args.directory, args.option)
                    + [
                        "show",
                        "--deps",
                        "none",
                        "--provenance",
                        *(["--history"] if args.history else []),
                        "--",
                        args.element,
                    ]
                ).stdout
            except ValueError as error:
                raise ValueError(
                    "Provenance query failed; this command requires this fork’s bst show --provenance. " + str(error)
                ) from error
            report = json.loads(text)
        report = select(
            validate_report(report),
            args.element if args.from_report else None,
            args.path,
            not args.no_follow,
            args.history,
        )
        print(
            json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2)
            if args.format == "json"
            else render_origin(report)
        )
        return 2 if any(e["unavailable_references"] for e in report["elements"]) else 0
    finally:
        runner.cancel()
