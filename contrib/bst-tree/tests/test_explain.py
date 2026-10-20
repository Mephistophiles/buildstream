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

from bst_tree.adapter import DETAIL_FIELDS, FIELDS, capture, parse_report
from bst_tree.cli import main
from bst_tree.diff import compare, has_changes
from bst_tree.explain import analyze, build_inputs, sections, unified
from bst_tree.model import Graph, read_snapshot, write_snapshot


def example():
    names = ["app.bst", "compiler.bst", "lib.bst", "runtime.bst", "unknown.bst"]
    nodes = {n: {"kind": "manual", "key": "a" * 64, "source_info": None, "workspace": False} for n in names}
    details = {n: {f: {} for f in DETAIL_FIELDS} for n in names}
    details["compiler.bst"]["config"] = {"build-commands": ["cc -O2"]}
    old = Graph(["app.bst"], nodes, {
        ("app.bst", "compiler.bst"): frozenset(["build"]),
        ("compiler.bst", "lib.bst"): frozenset(["run"]),
        ("app.bst", "runtime.bst"): frozenset(["run"]),
        ("app.bst", "unknown.bst"): frozenset(["build"]),
    }, {"strict": True, "bst_version": "2.8.0"}, details)
    return old, copy.deepcopy(old)


def records(old, new, **kwargs):
    return {r["name"]: r for r in analyze(old, new, **kwargs)["records"]}


def test_details_roundtrip_and_legacy(tmp_path):
    old, new = example()
    path = tmp_path / "details.json"
    write_snapshot(old, path)
    assert read_snapshot(path).as_dict() == old.as_dict()
    assert json.loads(path.read_text())["format_version"] == 2
    assert old.scoped("build").details.keys() == old.scoped("build").nodes.keys()
    new.details = None
    assert not has_changes(compare(old, new))
    assert "unavailable" in " ".join(analyze(old, new)["warnings"])
    new.nodes["app.bst"]["key"] = "b" * 64
    assert records(old, new)["app.bst"]["category"] == "unknown"
    write_snapshot(new, path)
    assert read_snapshot(path).format_version == 1


@pytest.mark.parametrize("mutation", [
    lambda d: d.pop("details"),
    lambda d: d["details"].pop("app.bst"),
    lambda d: d["details"]["app.bst"].update(config=[]),
    lambda d: d["details"]["app.bst"].update(extra={}),
    lambda d: d["details"]["app.bst"]["env"].update(bad=float("nan")),
    lambda d: d["details"]["app.bst"]["env"].update({1: "ambiguous"}),
])
def test_details_validation(mutation):
    old, _ = example()
    data = old.as_dict()
    mutation(data)
    with pytest.raises(ValueError):
        Graph.from_dict(data)


def test_local_change_and_dependency_candidates():
    old, new = example()
    new.details["compiler.bst"]["config"]["build-commands"] = ["cc -O3"]
    for name in ("compiler.bst", "app.bst", "unknown.bst"):
        new.nodes[name]["key"] = "b" * 64
    result = records(old, new)
    assert result["compiler.bst"]["category"] == "local"
    assert result["compiler.bst"]["evidence"] == ["config"]
    assert result["compiler.bst"]["consumers"] == ["app.bst"]
    assert result["app.bst"]["category"] == "dependency"
    assert {c["name"] for c in result["app.bst"]["candidates"]} == {"compiler.bst", "unknown.bst"}
    assert result["unknown.bst"]["category"] == "unknown"
    assert "-    \"cc -O2\"" in unified(old, new, "compiler.bst", "config")
    assert records(old, new, fields=["key"])["compiler.bst"]["category"] == "unknown"
    assert not has_changes(compare(old, new, fields=[]))


def test_runtime_closure_is_build_input_but_runtime_only_is_not():
    old, new = example()
    for name in ("lib.bst", "runtime.bst", "app.bst", "compiler.bst"):
        new.nodes[name]["key"] = "b" * 64
    result = records(old, new)
    assert [c["name"] for c in result["app.bst"]["candidates"]] == ["compiler.bst", "lib.bst"]
    assert result["compiler.bst"]["category"] == "unknown"
    # A changed runtime-only child cannot explain the parent's key.
    old, new = example()
    new.nodes["runtime.bst"]["key"] = "b" * 64
    new.nodes["app.bst"]["key"] = "b" * 64
    assert records(old, new)["app.bst"]["category"] == "unknown"


@pytest.mark.parametrize("strict,key", [(False, "a" * 64), (True, None)])
def test_no_attribution_without_strict_resolved_keys(strict, key):
    old, new = example()
    old.metadata["strict"] = strict
    old.nodes["app.bst"]["key"] = key
    new.nodes["compiler.bst"]["key"] = new.nodes["app.bst"]["key"] = "b" * 64
    assert records(old, new)["app.bst"]["category"] == "unknown"


def test_structural_changes_cycles_and_unresolved_inputs():
    old, new = example()
    new.edges["lib.bst", "compiler.bst"] = frozenset(["run"])
    assert build_inputs(new.adjacency(), "app.bst") == {"compiler.bst", "lib.bst", "unknown.bst"}
    new.nodes["app.bst"]["key"] = "b" * 64
    del new.edges["app.bst", "unknown.bst"]
    del new.nodes["unknown.bst"]
    del new.details["unknown.bst"]
    result = records(old, new)
    assert result["app.bst"]["category"] == "local"
    assert result["unknown.bst"]["category"] == "structural"
    assert {c["name"] for c in result["app.bst"]["candidates"]} == {"unknown.bst"}


def test_filtered_sections_hide_ignored_changes():
    old, new = example()
    new.nodes["compiler.bst"].update(key="b" * 64, source_info=[{"version": "new"}])
    new.details["compiler.bst"]["env"] = {"CFLAGS": "-O3"}
    assert sections(old, new, "compiler.bst", fields=["key"]) == ["key"]
    assert sections(old, new, "compiler.bst", fields=["details"]) == ["env"]
    assert "source_info" not in unified(old, new, "compiler.bst", "node", fields=["key"])


def detailed_report(marker):
    values = ["app.bst", "manual", "a" * 64, "[]", "", "[]", "[]",
              'build-commands:\n- "echo %{flags}"', "flags: '-O2'", "CFLAGS: '-O2'", "{}"]
    return marker + "record\n" + "".join(marker + f + "\n" + v + "\n"
           for f, v in zip(FIELDS + DETAIL_FIELDS, values)) + marker + "end\n"


def test_detailed_capture_is_one_framed_query():
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if "--version" in args:
            text = "2.8.0"
        elif "all" in args:
            marker = args[args.index("--format") + 1].split("record\n")[0]
            text = detailed_report(marker)
        else:
            text = "app.bst\n"
        return subprocess.CompletedProcess(args, 0, text)
    graph = capture(["app.bst"], run=run, detailed=True)
    assert len(calls) == 3
    assert graph.details["app.bst"]["env"] == {"CFLAGS": "-O2"}
    assert graph.format_version == 2
    with pytest.raises(ValueError, match="unsupported"):
        parse_report(detailed_report("M_").replace("CFLAGS: '-O2'", "%{env}"), "M_", detailed=True)


def test_cli_explanation_and_tty(tmp_path, capsys):
    old, new = example()
    new.details["compiler.bst"]["env"] = {"CFLAGS": "-O3"}
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_snapshot(old, a)
    write_snapshot(new, b)
    assert main(["diff", str(a), str(b), "--explain", "--format", "json", "--check"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["explanation"]["summary"] == {"local": 1}
    assert main(["diff", str(a), str(b), "--format", "tui"]) == 2
    assert "requires a terminal" in capsys.readouterr().err
    assert main(["diff", str(a), str(b), "--explain", "--ignore-fields", "details", "--check"]) == 0


def test_compact_key_graph_matches_reference_on_cycles():
    import random
    from bst_tree.keygraph import KeyGraph, members

    rng = random.Random(7)
    names = [f"n{i}" for i in range(12)]
    for _ in range(30):
        nodes = {n: {"kind": "manual", "key": "a", "source_info": None, "workspace": False} for n in names}
        edges = {(p, c): frozenset(rng.choice([("build",), ("run",), ("build", "run")]))
                 for p in names for c in names if rng.random() < 0.12}
        graph = Graph([names[0]], nodes, edges, {"strict": True})
        compact = KeyGraph(graph, names)
        expected = {n: build_inputs(graph.adjacency(), n) for n in names}
        for i, name in enumerate(names):
            assert set(members(compact.inputs[i], names)) == expected[name]
            assert set(members(compact.consumers[i], names)) == {p for p in names if name in expected[p]}


def test_large_shared_runtime_graph_keeps_related_lists_bounded():
    names = [f"n{i:04}" for i in range(1000)]
    nodes = {n: {"kind": "manual", "key": "a", "source_info": None, "workspace": False} for n in names}
    old = Graph([names[0]], nodes, {(p, c): frozenset(["build", "run"])
                                  for p, c in zip(names, names[1:])}, {"strict": True})
    new = copy.deepcopy(old)
    for node in new.nodes.values():
        node["key"] = "b"
    result = records(old, new, limit=10)
    assert result[names[0]]["candidate_count"] == 999
    assert len(result[names[0]]["candidates"]) == 10
    assert result[names[-1]]["consumer_count"] == 999
    assert len(result[names[-1]]["consumers"]) == 10
