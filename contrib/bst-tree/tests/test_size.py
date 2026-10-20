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
import threading
import pytest
from bst_tree.artifacts import Archive, ArtifactComparison
from bst_tree.cli import main
from bst_tree.model import Graph, write_snapshot
from bst_tree.size import size_report
from test_artifacts import archive, pair, FakeRunner


def graph():
    return Graph(
        ["a", "b"],
        {n: {"kind": "import", "key": None, "source_info": None, "workspace": False} for n in ("a", "b", "shared")},
        {("a", "shared"): frozenset(["run"]), ("b", "shared"): frozenset(["run"])},
        {},
    )


def test_size_counts_shared_nodes_once_and_content_separately(tmp_path):
    manifests = {}
    for name, files in [
        ("a", [("a", b"123"), ("copy", b"123")]),
        ("b", [("b", b"12345")]),
        ("shared", [("shared", b"123")]),
    ]:
        manifests[name] = Archive(archive(tmp_path / (name + ".tar"), files), threading.Event()).scan().entries
    report = size_report(graph(), manifests)
    assert report["total"]["file_bytes"] == 14 and report["total"]["content_unique_bytes"] == 8
    assert report["roots"][0]["closure"]["file_bytes"] == 9
    assert report["roots"][0]["exclusive"]["file_bytes"] == 6
    assert report["roots"][1]["exclusive"]["file_bytes"] == 5
    del manifests["shared"]
    report = size_report(graph(), manifests)
    assert not report["coverage"]["complete"] and report["total"]["file_bytes"] is None
    assert report["total"]["known_file_bytes"] == 11


def test_offline_size_cli_snapshot_budget_and_missing(tmp_path, capsys):
    g = graph()
    snapshot = tmp_path / "graph.json"
    write_snapshot(g, snapshot)
    args = ["size", "--snapshot", str(snapshot), "--format", "json"]
    for name in g.nodes:
        path = archive(tmp_path / (name + ".tar"), [(name, b"data")])
        args += ["--archive", name, str(path)]
    assert main([*args, "--check-max", "11"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["total"]["file_bytes"] == 12 and report["total"]["content_unique_bytes"] == 4
    assert main(args[:-3]) == 2
    assert json.loads(capsys.readouterr().out)["coverage"]["missing"] == ["shared"]
    assert main(["size", "--archive", "a", str(tmp_path / "a.tar")]) == 0
    assert "4 B" in capsys.readouterr().out


@pytest.mark.parametrize("code,status", [(0, "analyzed"), (1, "error"), (-9, "error")])
def test_bloaty_diff_direction_and_status(tmp_path, monkeypatch, code, status):
    monkeypatch.setattr("bst_tree.artifacts.shutil.which", lambda name: "/usr/bin/" + name)
    runner = FakeRunner(code=code)
    session = ArtifactComparison(
        *(str(p) for p in pair(tmp_path)), archives=True, runner=runner, bloaty_sources="sections,compileunits"
    )
    try:
        session.load()
        result = session.analyze("bloaty", "lib/libx.so")
        assert result["status"] == status
        argv = runner.calls[0][0]
        assert argv[argv.index("-d") + 1] == "sections,compileunits"
        assert "/after/" in argv[-3] and argv[-2] == "--" and "/before/" in argv[-1]
        with pytest.raises(ValueError):
            session.analyze("bloaty", "etc/config")
    finally:
        session.close()
