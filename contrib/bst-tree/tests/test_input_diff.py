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
import copy
import json
import subprocess

import pytest

from bst_tree.adapter import capture
from bst_tree.cli import main
from bst_tree.explain import analyze, render_explanation
from bst_tree.input_diff import input_changes, render_inputs
from bst_tree.model import DETAIL_FIELDS, Graph, read_snapshot, write_snapshot
from bst_tree.provenance import leaf_paths, lookup


def pair(history=True):
    def graph(flag, file, condition=False):
        details = {"app.bst": {field: {} for field in DETAIL_FIELDS}}
        details["app.bst"]["env"] = {"CFLAGS": flag}
        details["app.bst"]["vars"] = {"flags": flag}
        entries = []
        for path in leaf_paths(details["app.bst"]):
            value = lookup(details["app.bst"], path)[1]
            origin = {"file": file, "line": 12, "column": 3, "synthetic": False}
            entry = {
                "path": path,
                "value": value,
                "raw": "%{flags}" if path == "/env/CFLAGS" else value,
                "value_available": True,
                "origin": origin,
                "references": [{"variable": "flags", "path": "/vars/flags"}] if path == "/env/CFLAGS" else [],
            }
            if history:
                step = {"raw": entry["raw"], "origin": origin, "state": "effective", "operation": "define"}
                if condition and path == "/vars/flags":
                    step["conditions"] = [{"expression": "debug", "origin": origin}]
                entry["history"] = [step]
            entries.append(entry)
        return Graph(
            ["app.bst"],
            {"app.bst": {"kind": "manual", "key": "a" * 64, "workspace": False, "source_info": None}},
            {},
            {"strict": True},
            details,
            {"app.bst": {"element": "app.bst", "history_available": history, "entries": entries}},
        )

    return graph("-O2", "project.conf"), graph("-O0", "app.bst", True)


def test_snapshot_roundtrip_scope_and_validation(tmp_path):
    old, _ = pair()
    path = tmp_path / "snapshot.json"
    write_snapshot(old, path)
    assert read_snapshot(path).as_dict() == old.as_dict()
    assert read_snapshot(path).format_version == 3
    assert old.scoped("run").provenance == old.provenance
    for mutate in [
        lambda d: d.pop("provenance"),
        lambda d: d["provenance"].pop("app.bst"),
        lambda d: d["provenance"]["app.bst"].update(element="other.bst"),
        lambda d: d["provenance"]["app.bst"]["entries"].pop(),
        lambda d: d["provenance"]["app.bst"]["entries"][0].update(value="wrong"),
        lambda d: d["provenance"]["app.bst"]["entries"][0].pop("history"),
        lambda d: d.update(format_version=2),
    ]:
        data = copy.deepcopy(old.as_dict())
        mutate(data)
        with pytest.raises(ValueError):
            Graph.from_dict(data)


def test_compact_sources_conditions_and_explicit_history(tmp_path, capsys):
    old, new = pair()
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_snapshot(old, a)
    write_snapshot(new, b)
    assert main(["diff", str(a), str(b), "--explain", "--check"]) == 1
    text = capsys.readouterr().out
    assert "/env/CFLAGS" in text and 'Before: "-O2"' in text and 'After: "-O0"' in text
    assert "project.conf:12:3" in text and "app.bst:12:3" in text and "Applied condition" in text
    assert "(define)" not in text
    assert main(["diff", str(a), str(b), "--explain", "--format", "json"]) == 0
    change = json.loads(capsys.readouterr().out)["explanation"]["records"][0]["input_changes"][0]
    assert [s["path"] for s in change["new"]["sources"]] == ["/env/CFLAGS", "/vars/flags"]
    assert "history" not in change["new"]["sources"][0]
    assert main(["diff", str(a), str(b), "--explain", "--history"]) == 0
    assert "effective (define)" in capsys.readouterr().out
    assert main(["diff", str(a), str(b), "--history"]) == 2
    assert "requires --explain" in capsys.readouterr().err
    assert analyze(old, new, fields=["key"])["records"] == []
    # Location-only changes do not affect the selected resolved-input diff.
    new.details = copy.deepcopy(old.details)
    assert analyze(old, new)["records"] == []


def test_one_sided_capture_and_missing_history_are_honest():
    old, new = pair(False)
    old.provenance = None
    changes, count = input_changes(old, new, "app.bst", history=True)
    assert count == 2 and changes[0]["old"]["status"] == "not-captured"
    assert changes[0]["new"]["status"] == "captured"
    text = render_inputs(changes, history=True)
    assert "Source not captured" in text and "History not captured" in text
    old.details = None
    assert input_changes(old, new, "app.bst") == ([], 0)


def test_lists_null_empty_added_removed_and_escaped_paths():
    old, new = pair()
    old.provenance = new.provenance = None
    old.details["app.bst"]["config"] = {"a/b~c": None, "commands": ["one", "two"], "gone": {}}
    new.details["app.bst"]["config"] = {"a/b~c": [], "commands": ["insert", "one", "two"], "added": None}
    changes, _ = input_changes(old, new, "app.bst")
    by_path = {c["path"]: c for c in changes}
    assert by_path["/config/a~1b~0c"]["old"]["present"]
    assert by_path["/config/added"]["new"]["present"] and by_path["/config/added"]["new"]["value"] is None
    assert not by_path["/config/gone"]["new"]["present"]
    assert "/config/commands/0" not in by_path
    assert by_path["/config/commands"]["old"]["value"] == ["one", "two"]
    changes, count = input_changes(old, new, "app.bst", limit=1)
    assert len(changes) == 1 and count > 1


def test_snapshot_capture_validates_details_and_rejects_drift(monkeypatch):
    old, _ = pair()
    import bst_tree.adapter as adapter

    calls = []
    parsed = old.nodes, old.edges, old.details
    monkeypatch.setattr(adapter, "parse_report", lambda *a, **k: parsed)

    def run(args, **kwargs):
        calls.append(args)
        if "--version" in args:
            text = "2.8.0"
        elif "--provenance" in args:
            text = json.dumps(
                {"format_version": 1, "query": "input-provenance", "elements": list(old.provenance.values())}
            )
        else:
            text = "app.bst"
        return subprocess.CompletedProcess(args, 0, text)

    result = capture(["app.bst"], run=run, provenance=True, history=True)
    assert result.details == old.details and result.provenance == old.provenance
    assert len(calls) == 5 and sum("--provenance" in call for call in calls) == 1
    assert "--history" in next(call for call in calls if "--provenance" in call)
    count = 0

    def drift(*a, **k):
        nonlocal count
        count += 1
        return parsed if count == 1 else ({}, {}, {})

    monkeypatch.setattr(adapter, "parse_report", drift)
    with pytest.raises(ValueError, match="Project changed"):
        capture(["app.bst"], run=run, provenance=True)
    with pytest.raises(ValueError, match="requires --provenance"):
        capture(["app.bst"], run=run, history=True)


def test_winning_only_list_member_sources_follow_variables():
    old, new = pair(False)
    for graph, flag in [(old, "-O2"), (new, "-O0")]:
        graph.details["app.bst"]["config"] = {"commands": ["echo " + flag]}
        data = graph.provenance["app.bst"]
        data["entries"] = [e for e in data["entries"] if e["path"] != "/config"]
        data["entries"].append(
            {
                "path": "/config/commands/0",
                "value": "echo " + flag,
                "raw": "echo %{flags}",
                "value_available": True,
                "origin": {"file": "commands.yml", "line": 4, "column": 5},
                "references": [{"variable": "flags", "path": "/vars/flags"}],
            }
        )
        Graph.from_dict(graph.as_dict())
    changes, _ = input_changes(old, new, "app.bst")
    command = next(c for c in changes if c["path"] == "/config/commands")
    assert [s["path"] for s in command["old"]["sources"]] == ["/config/commands/0", "/vars/flags"]
    assert command["new"]["sources"][0]["origin"]["file"] == "commands.yml"
    assert not command["old"]["history_available"]
    # Even a reference cycle cannot cause repeated expansion.
    variable = next(e for e in new.provenance["app.bst"]["entries"] if e["path"] == "/vars/flags")
    variable["references"] = [{"variable": "flags", "path": "/vars/flags"}]
    assert len(input_changes(old, new, "app.bst")[0][0]["new"]["sources"]) == 2
