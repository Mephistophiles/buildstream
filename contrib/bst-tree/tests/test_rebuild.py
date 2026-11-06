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

import pytest

from bst_tree.cli import main
from bst_tree.headless import query_graph
from bst_tree.model import Graph, write_snapshot


def graph():
    names = ("image.bst", "a.bst", "b.bst", "vfs.bst", "side.bst", "tool.bst", "outside.bst")
    return Graph(
        ["image.bst", "outside.bst"],
        {n: {"kind": "manual", "key": None, "source_info": None, "workspace": False} for n in names},
        {
            edge: frozenset(["build"])
            for edge in (
                ("image.bst", "a.bst"),
                ("image.bst", "b.bst"),
                ("image.bst", "side.bst"),
                ("a.bst", "vfs.bst"),
                ("b.bst", "vfs.bst"),
                ("a.bst", "tool.bst"),
                ("vfs.bst", "tool.bst"),
                ("outside.bst", "vfs.bst"),
            )
        },
        {},
    )


def test_rebuild_selects_union_of_paths_not_other_consumers_or_dependencies():
    g = graph()
    report = query_graph(g, "rebuild", source="image.bst", element="vfs.bst")
    assert report["elements"] == ["vfs.bst", "a.bst", "b.bst", "image.bst"]
    assert report["cache_checked"] is False
    assert len(report["edges"]) == 4
    assert g.targets == ["image.bst", "outside.bst"]
    assert query_graph(g, "rebuild", source="a.bst", element="vfs.bst")["elements"] == ["vfs.bst", "a.bst"]
    assert query_graph(g, "rebuild", source="vfs.bst", element="vfs.bst")["elements"] == ["vfs.bst"]
    assert query_graph(g, "rebuild", source="vfs.bst", element="image.bst")["elements"] == []
    assert query_graph(g, "rebuild", source="image.bst", element="vfs.bst", scope="run")["elements"] == []
    for source, destination in [("missing", "vfs.bst"), ("image.bst", "missing")]:
        with pytest.raises(ValueError, match="absent"):
            query_graph(g, "rebuild", source=source, element=destination)


def test_rebuild_cycles_fail_only_on_selected_corridor():
    g = graph()
    g.edges["side.bst", "side.bst"] = frozenset(["run"])
    assert query_graph(g, "rebuild", source="image.bst", element="vfs.bst")["elements"]
    g.edges["vfs.bst", "a.bst"] = frozenset(["run"])
    with pytest.raises(ValueError, match="cycle"):
        query_graph(g, "rebuild", source="image.bst", element="vfs.bst")


def test_rebuild_formats_and_limit(tmp_path, capsys):
    path = tmp_path / "graph.json"
    write_snapshot(graph(), path)
    args = ["rebuild", "image.bst", "vfs.bst", "--snapshot", str(path)]
    assert main(args) == 0
    assert capsys.readouterr().out == "vfs.bst a.bst b.bst image.bst\n"
    assert main(args + ["--format", "chains"]) == 0
    assert capsys.readouterr().out == "image.bst -> a.bst -> vfs.bst\nimage.bst -> b.bst -> vfs.bst\n"
    assert main(args + ["--format", "chains", "--max-chains", "1"]) == 3
    assert "limit reached" in capsys.readouterr().err
    # Limiting chain display must never truncate the element selection.
    assert main(args + ["--max-chains", "1", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["elements"] == ["vfs.bst", "a.bst", "b.bst", "image.bst"]
    assert main(["rebuild", "vfs.bst", "image.bst", "--snapshot", str(path)]) == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("command", ["path", "chains", "rebuild"])
def test_global_and_local_options_reach_live_capture(command, monkeypatch, capsys):
    calls = []

    def capture(targets, directory, options, **kwargs):
        calls.append((targets, options))
        return graph()

    monkeypatch.setattr("bst_tree.headless.capture", capture)
    assert (
        main(
            [
                "-o",
                "arch",
                "aarch64",
                "--option",
                "mode",
                "path",
                command,
                "image.bst",
                "-o",
                "debug",
                "true",
                "vfs.bst",
            ]
        )
        == 0
    )
    assert calls == [(["image.bst", "vfs.bst"], [["arch", "aarch64"], ["mode", "path"], ["debug", "true"]])]
    capsys.readouterr()


def test_snapshot_output_and_global_option_do_not_conflict(monkeypatch, capsys):
    def capture(targets, directory, options, **kwargs):
        assert options == [["arch", "aarch64"], ["debug", "true"]]
        return graph()

    monkeypatch.setattr("bst_tree.cli.capture", capture)
    assert main(["-o", "arch", "aarch64", "snapshot", "image.bst", "--option", "debug", "true", "-o", "-"]) == 0
    assert json.loads(capsys.readouterr().out)["targets"]


def test_global_options_rejected_for_offline_queries(tmp_path, capsys):
    path = tmp_path / "graph.json"
    write_snapshot(graph(), path)
    assert main(["-o", "arch", "aarch64", "rebuild", "image.bst", "vfs.bst", "--snapshot", str(path)]) == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert main(["-o", "arch", "aarch64", "diff", str(path), str(path)]) == 2
    assert "not supported" in capsys.readouterr().err
