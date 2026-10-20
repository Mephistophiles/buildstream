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
from .adapter import command_base
from .headless import HeadlessRunner, project_arguments


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
    project_arguments(p)
    p.set_defaults(command="origin")
    return p


def validate_report(report):
    if (
        not isinstance(report, dict)
        or report.get("format_version") != 1
        or report.get("query") != "input-provenance"
        or not isinstance(report.get("elements"), list)
    ):
        raise ValueError("Unsupported provenance report; use bst show --provenance from this fork")
    seen = set()
    for element in report["elements"]:
        if not isinstance(element, dict) or not isinstance(element.get("element"), str) or element["element"] in seen:
            raise ValueError("Invalid provenance element")
        seen.add(element["element"])
        paths = set()
        if not isinstance(element.get("entries"), list):
            raise ValueError("Invalid provenance entries")
        for entry in element["entries"]:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("path"), str)
                or not entry["path"].startswith("/")
                or entry["path"] in paths
            ):
                raise ValueError("Invalid/duplicate provenance path")
            paths.add(entry["path"])
            if (
                not isinstance(entry.get("origin"), dict)
                or type(entry.get("value_available")) is not bool
                or not isinstance(entry.get("references"), list)
            ):
                raise ValueError("Invalid provenance evidence")
            for ref in entry["references"]:
                if (
                    not isinstance(ref, dict)
                    or not isinstance(ref.get("variable"), str)
                    or not isinstance(ref.get("path"), str)
                ):
                    raise ValueError("Invalid variable reference")
    return report


def select(report, element=None, paths=(), follow=True):
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
        result["elements"].append(
            {
                **data,
                "entries": [dict(by_path[p], selected=p in requested) for p in sorted(selected)],
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
    runner = HeadlessRunner(args.timeout, args.output_limit)
    try:
        if args.from_report:
            with open(args.from_report, encoding="utf-8") as stream:
                report = json.load(stream)
        else:
            try:
                text = runner(
                    command_base(args.directory, args.option)
                    + ["show", "--deps", "none", "--provenance", "--", args.element]
                ).stdout
            except ValueError as error:
                raise ValueError(
                    "Provenance query failed; this command requires this fork’s bst show --provenance. " + str(error)
                ) from error
            report = json.loads(text)
        report = select(
            validate_report(report), args.element if args.from_report else None, args.path, not args.no_follow
        )
        print(
            json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2)
            if args.format == "json"
            else render_origin(report)
        )
        return 2 if any(e["unavailable_references"] for e in report["elements"]) else 0
    finally:
        runner.cancel()
