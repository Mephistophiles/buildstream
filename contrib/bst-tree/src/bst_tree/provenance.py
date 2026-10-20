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
"""Shared validation for exported and snapshot input provenance."""

import json
import re


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
                or re.search(r"~(?![01])", entry["path"])
                or entry["path"] in paths
            ):
                raise ValueError("Invalid/duplicate provenance path")
            paths.add(entry["path"])
            if (
                not isinstance(entry.get("origin"), dict)
                or "raw" not in entry
                or "value" not in entry
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
            if "history" in entry:
                if not isinstance(entry["history"], list) or not entry["history"]:
                    raise ValueError("Invalid provenance history")
                for step in entry["history"]:
                    if (
                        not isinstance(step, dict)
                        or "raw" not in step
                        or not isinstance(step.get("origin"), dict)
                        or step.get("operation") not in ("define", "generated", "compose-list")
                        or step.get("state") not in ("overridden", "effective", "contributes")
                    ):
                        raise ValueError("Invalid provenance history step")
                    conditions = step.get("conditions", [])
                    if not isinstance(conditions, list):
                        raise ValueError("Invalid history conditions")
                    for condition in conditions:
                        if (
                            not isinstance(condition, dict)
                            or not isinstance(condition.get("expression"), str)
                            or not isinstance(condition.get("origin"), dict)
                        ):
                            raise ValueError("Invalid history condition")
    return report


def pointer(part):
    return str(part).replace("~", "~0").replace("/", "~1")


def lookup(value, path):
    """Return presence separately from null; reject ambiguous list indices."""
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", part) and int(part) < len(value):
            value = value[int(part)]
        else:
            return False, None
    return True, value


def leaf_paths(value, prefix=""):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from leaf_paths(child, prefix + "/" + pointer(key))
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from leaf_paths(child, prefix + "/" + str(index))
    else:
        yield prefix


def validate_snapshot_provenance(provenance, nodes, details):
    if not isinstance(provenance, dict) or set(provenance) != set(nodes):
        raise ValueError("Snapshot provenance must cover exactly the graph nodes")
    validate_report({"format_version": 1, "query": "input-provenance", "elements": list(provenance.values())})
    for name, element in provenance.items():
        if element["element"] != name or type(element.get("history_available")) is not bool:
            raise ValueError("Invalid snapshot provenance element/history coverage")
        entries = {entry["path"]: entry for entry in element["entries"]}
        if not set(leaf_paths(details[name])) <= entries.keys():
            raise ValueError("Snapshot provenance does not cover resolved input leaves")
        for path, entry in entries.items():
            present, value = lookup(details[name], path)
            if not present or not entry["value_available"] or value != entry["value"]:
                raise ValueError(f"Snapshot provenance disagrees with resolved inputs: {name} {path}")
            if element["history_available"] and "history" not in entry:
                raise ValueError("Snapshot provenance lacks declared history")
            if not element["history_available"] and "history" in entry:
                raise ValueError("Snapshot provenance history is not declared")
            if any(ref["path"] not in entries for ref in entry["references"]):
                raise ValueError("Snapshot provenance has unavailable variable references")
    try:
        json.dumps(provenance, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid snapshot provenance values") from error
