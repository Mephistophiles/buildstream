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
"""Evidence-based change analysis; public show fields are not the complete key recipe."""

from collections import Counter, deque
import difflib
import json

from .diff import compare
from .model import DETAIL_FIELDS
from .keygraph import KeyGraph, members

LIMITATION = (
    "Observed inputs and dependency candidates, not proof of cache-key causality. "
    "Plugin unique keys, sandbox settings and nocache environment rules are not fully captured. "
    "Workspace file contents are not captured."
)
CATEGORY_LABELS = {
    "local": "Observed inputs", "dependency": "Dependency candidates",
    "unknown": "Unexplained", "structural": "Added / removed", "unchanged": "Unchanged",
}


def build_inputs(adjacency, name):
    """BuildStream BUILD scope: direct build dependencies and their RUN closure."""
    pending = deque(child for child, kinds in adjacency[name] if "build" in kinds)
    seen = set()
    while pending:
        child = pending.popleft()
        if child in seen:
            continue
        seen.add(child)
        pending.extend(n for n, kinds in adjacency[child] if "run" in kinds)
    seen.discard(name)
    return seen


def analyze(old, new, *, fields=None, limit=None):
    delta = compare(old, new, fields)
    changes = {item["name"]: item for item in delta["nodes"]}
    edge_changes = {}
    for edge in delta["edges"]:
        edge_changes.setdefault(edge["from"], []).append(edge)
    names = sorted(old.nodes.keys() | new.nodes.keys())
    index = {name: i for i, name in enumerate(names)}
    strict = all(g.metadata.get("strict") is True for g in (old, new))
    records = {}
    key_parents = 0
    local_mask = 0
    for name in sorted(changes.keys() | edge_changes.keys()):
        change = changes.get(name, {})
        before, after = old.nodes.get(name), new.nodes.get(name)
        evidence = []
        selected_old, selected_new = change.get("old") or {}, change.get("new") or {}
        if before is not None and after is not None:
            evidence = [f for f in ("kind", "source_info", "workspace")
                        if f in selected_old and selected_old[f] != selected_new[f]]
            if "details" in selected_old:
                evidence += [f for f in DETAIL_FIELDS
                             if selected_old["details"][f] != selected_new["details"][f]]
        if edge_changes.get(name):
            evidence.append("dependencies")
        key_changed = ("key" in selected_old and "key" in selected_new
                       and selected_old["key"] != selected_new["key"])
        resolved = bool(before and after and before["key"] is not None and after["key"] is not None)
        if key_changed and resolved and strict:
            key_parents |= 1 << index[name]
        if evidence:
            local_mask |= 1 << index[name]
        records[name] = {
            "name": name, "category": "structural" if before is None or after is None else
            "local" if evidence else "unknown", "evidence": evidence,
            "key_changed": key_changed, "keys_resolved": resolved,
            "candidates": [], "candidate_count": 0,
            "edges": edge_changes.get(name, []), "change": change.get("change", "modified"),
            "consumers": [], "consumer_count": 0,
        }
    if key_parents:
        old_graph, new_graph = KeyGraph(old, names), KeyGraph(new, names)
        changed_keys = sum(1 << index[n] for n in old.nodes.keys() & new.nodes.keys()
                           if old.nodes[n]["key"] is not None and new.nodes[n]["key"] is not None
                           and old.nodes[n]["key"] != new.nodes[n]["key"])
        for name, record in records.items():
            i = index[name]
            before, after = old_graph.inputs[i], new_graph.inputs[i]
            candidates = ((before ^ after) | ((before & after) & changed_keys)) if key_parents & (1 << i) else 0
            record["candidate_count"] = candidates.bit_count()
            # Show observed origins first; do not bury them under intermediary keys.
            origins = members(candidates & local_mask, names, limit)
            remaining = None if limit is None else limit - len(origins)
            candidates_shown = origins + members(candidates & ~local_mask, names, remaining)
            record["candidates"] = [{
                "name": child,
                "reason": "build input added / removed" if (before ^ after) & (1 << index[child])
                else "build input key changed",
            } for child in candidates_shown]
            if candidates and record["category"] == "unknown":
                record["category"] = "dependency"
            before, after = old_graph.consumers[i], new_graph.consumers[i]
            consumers = ((before ^ after) | ((before | after) if changed_keys & (1 << i) else 0)) & key_parents
            record["consumer_count"] = consumers.bit_count()
            record["consumers"] = members(consumers, names, limit)
    warnings = [LIMITATION]
    if old.details is None or new.details is None:
        warnings.append("Detailed inputs are unavailable on one or both sides; use snapshot --details for both snapshots.")
    if not strict:
        warnings.append("Strict mode is not confirmed on both sides; dependency-key attribution is disabled.")
    if old.metadata != new.metadata:
        warnings.append("Snapshot context differs (BuildStream version / project options / strict mode); inspect Context.")
    return {
        "format_version": 1, "summary": dict(Counter(r["category"] for r in records.values())),
        "records": list(records.values()), "warnings": warnings, "related_limit": limit,
    }


def value_lines(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).splitlines()


def section_values(old, new, name, section, fields=None):
    def value(graph):
        if name is None:
            return {"targets": graph.targets, "metadata": graph.metadata}
        if name not in graph.nodes:
            return None
        if section in DETAIL_FIELDS:
            return graph.details[name][section] if graph.details is not None else None
        if section == "dependencies":
            return {child: sorted(kinds) for child, kinds in graph.adjacency()[name]}
        if section == "key":
            return graph.nodes[name]["key"]
        return {k: v for k, v in graph.nodes[name].items()
                if k != "key" and (fields is None or k in fields)}
    return value(old), value(new)


def unified(old, new, name, section, *, full=False, fields=None):
    before, after = section_values(old, new, name, section, fields)
    if before == after:
        return "No changes in this section."
    return "\n".join(difflib.unified_diff(
        value_lines(before), value_lines(after), fromfile="before/" + section,
        tofile="after/" + section, lineterm="", n=1000000 if full else 3,
    ))


def sections(old, new, name, fields=None):
    selected = set(fields) if fields is not None else None
    available = ["node", "dependencies"]
    if selected is None or "details" in selected:
        if old.details is not None and new.details is not None:
            available += list(DETAIL_FIELDS)
    if selected is None or "key" in selected:
        available.append("key")
    return [s for s in available if section_values(old, new, name, s, fields)[0] != section_values(old, new, name, s, fields)[1]]


def overview(record):
    lines = [CATEGORY_LABELS[record["category"]], ""]
    if record["evidence"]:
        lines.append("Changed: " + ", ".join(record["evidence"]))
    if record["key_changed"]:
        lines.append("Cache key changed." if record["keys_resolved"] else "Cache key became resolved / unresolved.")
    elif record["category"] != "structural":
        lines.append("No cache-key change in the selected comparison.")
    if record["category"] == "unknown":
        lines.append("No captured local change or supported dependency candidate explains this change.")
    candidates = record["candidates"]
    if candidates:
        lines += ["", f"Build input candidates ({record['candidate_count']}):"]
        lines += [f"  {c['name']} — {c['reason']}" for c in candidates[:8]]
        if record["candidate_count"] > 8:
            lines.append(f"  … {record['candidate_count'] - 8} more; choose Dependency candidates to inspect.")
    if record["consumer_count"]:
        lines += ["", f"Candidate for {record['consumer_count']} changed consumer(s)."]
        lines += ["  " + name for name in record["consumers"][:5]]
    lines += ["", "Candidates are not proven causes. See Snapshot context for coverage."]
    return "\n".join(lines)


def render_explanation(report):
    counts = report["summary"]
    lines = ["Change analysis: " + "; ".join(f"{CATEGORY_LABELS[c]}: {counts.get(c, 0)}"
             for c in ("local", "dependency", "unknown", "structural"))]
    for category in ("local", "unknown", "structural", "dependency"):
        entries = [r for r in report["records"] if r["category"] == category]
        for record in entries[:10]:
            reason = ", ".join(record["evidence"]) or f"{record['candidate_count']} dependency candidate(s)"
            lines.append(f"  {record['name']}: {reason}")
        if len(entries) > 10:
            lines.append(f"  … {len(entries) - 10} more {CATEGORY_LABELS[category].lower()}; use --format tui or json")
    lines.extend("Note: " + warning for warning in report["warnings"])
    return "\n".join(lines)
