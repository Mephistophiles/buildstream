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
import json
import subprocess

import pytest

from bst_tree.adapter import FIELDS, capture, parse_report
from bst_tree.cli import main
from bst_tree.diff import compare, has_changes, render_tree
from bst_tree.model import Graph, read_snapshot, write_snapshot


def node(key=None):
    return {"kind": "manual", "key": key, "source_info": None, "workspace": False}


def graph():
    return Graph(
        ["app.bst"],
        {n: node() for n in ("app.bst", "compiler.bst", "sub:lib.bst", "bootstrap.bst")},
        {
            ("app.bst", "compiler.bst"): frozenset(["build"]),
            ("app.bst", "sub:lib.bst"): frozenset(["run"]),
            ("compiler.bst", "sub:lib.bst"): frozenset(["run"]),
            ("compiler.bst", "bootstrap.bst"): frozenset(["build"]),
        },
        {"bst_version": "test"},
    )


def test_scope_and_paths():
    g = graph()
    assert set(g.scoped("run").nodes) == {"app.bst", "sub:lib.bst"}
    assert set(g.scoped("build").nodes) == {"app.bst", "compiler.bst", "sub:lib.bst"}
    assert g.paths_to("sub:lib.bst") == [["app.bst", "sub:lib.bst"]]
    g.targets.append("compiler.bst")
    assert len(g.paths_to("sub:lib.bst")) == 2


def test_roundtrip_and_determinism(tmp_path):
    g = graph()
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    write_snapshot(g, first)
    write_snapshot(read_snapshot(first), second)
    assert first.read_bytes() == second.read_bytes()
    assert not has_changes(compare(g, read_snapshot(first)))
    assert list(tmp_path.iterdir()) == [first, second]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(format_version=2),
        lambda d: d.update(targets=["missing"]),
        lambda d: d["edges"][0].update(to="missing"),
        lambda d: d["edges"][0].update(types=["unknown"]),
        lambda d: d["nodes"]["app.bst"].update(workspace="yes"),
    ],
)
def test_invalid_snapshot(mutate):
    data = graph().as_dict()
    mutate(data)
    with pytest.raises(ValueError):
        Graph.from_dict(data)


def test_diff_edge_and_source_changes():
    old = graph()
    new = Graph.from_dict(old.as_dict())
    new.nodes["sub:lib.bst"] = {**node("a" * 64), "source_info": [{"version": "new"}]}
    new.edges["app.bst", "compiler.bst"] = frozenset(["build", "run"])
    del new.edges["compiler.bst", "bootstrap.bst"]
    del new.nodes["bootstrap.bst"]
    delta = compare(old, new)
    assert delta["summary"]["nodes_modified"] == 1
    assert delta["summary"]["nodes_removed"] == 1
    assert delta["summary"]["edges_modified"] == 1
    output = render_tree(old, new, delta)
    assert "build -> build/run" in output
    assert "- bootstrap.bst" in output
    assert "source_info" in output
    assert "(see above)" in output


def test_cycle_is_bounded():
    g = graph()
    g.edges["sub:lib.bst", "app.bst"] = frozenset(["run"])
    assert len(render_tree(g, g, compare(g, g), show_all=True).splitlines()) < 15
    assert g.paths_to("bootstrap.bst")


def report(marker, name="app.bst", build="[]", runtime="[]"):
    values = [
        name,
        "manual",
        "?" * 64,
        "- kind: git\n  version: abc\n  extra-data:\n    note: |\n      multi\n      line",
        "",
        build,
        runtime,
    ]
    return (
        marker
        + "record\n"
        + "".join(marker + f + "\n" + v + "\n" for f, v in zip(FIELDS, values))
        + marker
        + "end\n\n"
    )


def test_multiline_report():
    nodes, edges = parse_report(report("MARK_", build="- app.bst", runtime="- app.bst"), "MARK_")
    assert nodes["app.bst"]["source_info"][0]["extra-data"]["note"] == "multi\nline\n"
    assert nodes["app.bst"]["key"] is None
    assert edges["app.bst", "app.bst"] == {"build", "run"}


def test_capture_public_cli():
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "--version" in args:
            output = "bst 2.test\n"
        elif "all" in args:
            fmt = args[args.index("--format") + 1]
            marker = fmt.split("record\n")[0]
            output = report(marker)
        else:
            output = "app.bst\n"
        return subprocess.CompletedProcess(args, 0, output)

    result = capture(["app.bst"], "/project", [("arch", "x86_64")], run=run)
    assert result.targets == ["app.bst"]
    assert result.metadata["options"] == {"arch": "x86_64"}
    assert all("--no-colors" in c and "--strict" in c for c in calls)


def test_cli_diff_exit_codes(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    old, new = graph(), graph()
    new.nodes["app.bst"]["key"] = "a" * 64
    write_snapshot(old, a)
    write_snapshot(new, b)
    assert main(["diff", str(a), str(b), "--check", "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["summary"]["nodes_modified"] == 1
    assert main(["diff", str(a), str(b)]) == 0
    assert main(["diff", str(a), str(a), "--check"]) == 0
    assert main(["diff", str(a), str(tmp_path / "missing")]) == 2
    assert main(["browse", "app.bst"]) == 2


def test_capture_errors():
    def fail(args, **kwargs):
        return subprocess.CompletedProcess(args, 17, "")

    with pytest.raises(ValueError, match="exit 17"):
        capture(["app.bst"], run=fail)
    with pytest.raises(ValueError, match="boundary"):
        parse_report(report("M_").replace("M_kind", "wrong"), "M_")
    with pytest.raises(ValueError, match="Malformed YAML"):
        parse_report(report("M_", build="[bad"), "M_")
    with pytest.raises(ValueError, match="unsupported"):
        parse_report(report("M_").replace("manual", "%{kind}"), "M_")


def test_added_branch_and_metadata():
    old, new = graph(), graph()
    new.nodes["extra.bst"] = node()
    new.edges["app.bst", "extra.bst"] = frozenset(["run"])
    new.metadata = {"bst_version": "new"}
    delta = compare(old, new)
    text = render_tree(old, new, delta)
    assert "+ extra.bst" in text
    assert "compiler.bst" not in text
    assert "compiler.bst" in render_tree(old, new, delta, show_all=True)
    assert delta["metadata"]["metadata"]["old"] == old.metadata


def test_key_only_is_not_source_change():
    old, new = graph(), graph()
    new.nodes["app.bst"]["key"] = "b" * 64
    output = render_tree(old, new, compare(old, new))
    assert "key:" in output
    assert "source_info:" not in output


def test_atomic_write_preserves_previous_on_error(tmp_path):
    path = tmp_path / "snapshot.json"
    write_snapshot(graph(), path)
    before = path.read_bytes()
    broken = graph()
    broken.metadata["invalid"] = object()
    with pytest.raises(TypeError):
        write_snapshot(broken, path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]
