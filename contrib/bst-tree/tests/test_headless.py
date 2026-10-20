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
import os
from pathlib import Path
import subprocess
import sys

import pytest

from bst_tree.cli import main
from bst_tools_core.process import CliRunner
from bst_tree.headless import query_graph, render_graph
from bst_tree.model import Graph, write_snapshot
from bst_tools_core.process import Execution
from bst_tree.inspection import ArtifactEntry, Inspection


def test_artifact_comparison_is_not_a_tree_command(capsys):
    with pytest.raises(SystemExit) as error:
        main(["artifact-diff", "old", "new"])
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def graph():
    names = ("app", "tool", "mid", "lib", "bootstrap", "other")
    return Graph(
        ["app", "other"],
        {n: {"kind": "manual", "key": None, "source_info": None, "workspace": False} for n in names},
        {
            ("app", "tool"): frozenset(["build"]),
            ("app", "mid"): frozenset(["run"]),
            ("mid", "lib"): frozenset(["run"]),
            ("tool", "lib"): frozenset(["run"]),
            ("tool", "bootstrap"): frozenset(["build"]),
        },
        {},
    )


def test_graph_scopes_shortest_paths_reverse_and_direct():
    g = graph()
    run = query_graph(g, "tree", scope="run")["graph"]
    assert set(run["nodes"]) == {"app", "other", "mid", "lib"}
    build = query_graph(g, "tree", scope="build")["graph"]
    assert set(build["nodes"]) == {"app", "other", "tool", "lib"}
    paths = query_graph(g, "paths", element="lib")["paths"]
    assert len(paths) == 1 and paths[0]["nodes"] == ["app", "mid", "lib"]
    assert paths[0]["edges"][0]["types"] == ["run"]
    reverse = query_graph(g, "rdeps", element="lib")
    assert reverse["consumers"] == ["app", "mid", "tool"]
    assert {e["from"] for e in reverse["graph"]["edges"]} == {"app", "mid", "tool"}
    # Direct rdeps contains only edges to the queried element, even if two
    # immediate consumers also depend on each other.
    g.edges["mid", "tool"] = frozenset(["run"])
    direct = query_graph(g, "rdeps", element="lib", direct=True)
    assert direct["consumers"] == ["mid", "tool"]
    assert all(e["to"] == "lib" for e in direct["graph"]["edges"])
    absent = query_graph(g, "rdeps", element="bootstrap", scope="run")
    assert absent["in_scope"] is False and absent["consumers"] == []
    assert query_graph(g, "paths", element="bootstrap", scope="build")["paths"] == []


def test_filtered_tree_keeps_context_cycles_are_bounded():
    g = graph()
    g.edges["lib", "app"] = frozenset(["run"])
    result = query_graph(g, "tree", match="LiB")
    assert result["matches"] == ["lib"]
    assert set(result["graph"]["nodes"]) == {"app", "mid", "tool", "lib"}
    text = render_graph(result)
    assert "already shown" in text and len(text.splitlines()) < 15
    assert "No matching" in render_graph(query_graph(g, "tree", match="absent"))
    assert "No consumers" in render_graph(query_graph(g, "rdeps", element="other"))
    with pytest.raises(ValueError, match="absent"):
        query_graph(g, "paths", element="missing")


@pytest.mark.parametrize("command,extra", [("tree", []), ("rdeps", ["lib"]), ("paths", ["lib"])])
def test_offline_cli_json_and_text(tmp_path, capsys, command, extra):
    path = tmp_path / "snapshot.json"
    write_snapshot(graph(), path)
    assert main([command, *extra, "--snapshot", str(path), "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["query"] == command and report["format_version"] == 1
    assert main([command, *extra, "--snapshot", str(path)]) == 0
    assert "lib" in capsys.readouterr().out
    assert main([command, *extra, "--snapshot", str(path), "app"]) == 2
    assert not capsys.readouterr().out
    assert main([command, *extra]) == 2
    assert not capsys.readouterr().out


def test_live_capture_options_timeout_and_stderr_separation(monkeypatch, capsys):
    calls = []

    def capture(targets, directory, options, *, run):
        calls.append((targets, directory, options))
        run(["bst", "show"])
        return graph()

    monkeypatch.setattr("bst_tree.headless.capture", capture)
    monkeypatch.setattr(
        "bst_tools_core.process.CommandRunner.__call__", lambda self, args, **kw: Execution(0, "data", "progress\n")
    )
    assert main(["tree", "app", "-C", "/project", "--option", "arch", "aarch64", "--format", "json"]) == 0
    captured = capsys.readouterr()
    assert calls == [(["app"], "/project", [["arch", "aarch64"]])]
    assert json.loads(captured.out)["graph"]["nodes"]
    assert captured.err == "progress\n"


@pytest.mark.parametrize(
    "result,message",
    [
        (Execution(1, "", "failure"), "failure"),
        (Execution(None, timed_out=True), "timed out"),
        (Execution(0, "partial", truncated=True), "output-limit"),
    ],
)
def test_bounded_runner_rejects_incomplete_results(monkeypatch, result, message):
    monkeypatch.setattr("bst_tools_core.process.CommandRunner.__call__", lambda self, args, **kw: result)
    runner = CliRunner()
    try:
        with pytest.raises(ValueError, match=message):
            runner(["bst"])
    finally:
        runner.cancel()


class Inspector:
    instances = []
    source_root = None

    def __init__(self, *args, **kwargs):
        self.cancelled = False
        self.calls = []
        self.__class__.instances.append(self)

    def show(self, name, fmt):
        self.calls.append(fmt)
        return {
            "%{config}": "build-commands: [echo hello]",
            "%{vars}": "flags: -O2",
            "%{env}": "CFLAGS: -O2",
            "%{public}": "bst: {}",
            "%{source-info}": "- kind: local\n  path: src\n",
            "%{workspace-dirs}": f"Workspace: {self.source_root}\n" if self.source_root else "",
        }[fmt]

    def load(self, name, section):
        self.calls.append("list")
        return Inspection("listing", artifacts=[ArtifactEntry("config", "regular 0644")])

    def pull_artifact(self, name):
        self.calls.append("pull")
        return self.load(name, "artifacts")

    def preview_artifact(self, name, path, limit, *, structured=False):
        self.calls.append((path, limit))
        return {"text": "hello", "binary": False, "truncated": False, "limit_bytes": limit}

    def checkout_sources(self, name):
        self.calls.append("checkout")
        return Inspection("checkout", workspace=self.source_root)

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def inspector(monkeypatch):
    Inspector.instances = []
    Inspector.source_root = None
    monkeypatch.setattr("bst_tree.headless_inspection.ProjectInspector", Inspector)
    return Inspector


def test_inspection_build_is_structured_and_cleans_up(inspector, capsys):
    assert main(["inspect", "app", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["details"]["config"]["build-commands"] == ["echo hello"]
    assert data["details"]["vars"]["flags"] == "-O2"
    assert inspector.instances[0].cancelled


def test_source_inspection_explicit_checkout_and_hidden_files(tmp_path, inspector, capsys):
    assert main(["inspect", "app", "--section", "sources", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["origin"] == "unavailable"
    assert "checkout" not in inspector.instances[-1].calls
    assert main(["inspect", "app", "--section", "sources", "--files"]) == 2
    assert "--checkout" in capsys.readouterr().err
    inspector.source_root = tmp_path
    (tmp_path / ".hidden").write_text("contents")
    (tmp_path / "link").symlink_to(".hidden")
    assert (
        main(
            [
                "inspect",
                "app",
                "--section",
                "sources",
                "--files",
                "--checkout",
                "--path",
                ".hidden",
                "--limit",
                "4",
                "--format",
                "json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["origin"] == "checkout" and data["checkout_removed_on_exit"]
    assert [f["path"] for f in data["files"]] == [".hidden", "link"]
    assert data["preview"]["text"].startswith("cont") and data["preview"]["truncated"]
    assert main(["inspect", "app", "--section", "sources", "--path", "link"]) == 2
    assert "symlinks" in capsys.readouterr().err
    assert inspector.instances[-1].cancelled


def test_artifact_inspection_explicit_pull_and_preview(inspector, capsys):
    assert main(["inspect", "app", "--section", "artifacts", "--path", "config", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["files"][0]["path"] == "config" and data["preview"]["text"] == "hello"
    assert "pull" not in inspector.instances[-1].calls
    assert main(["inspect", "app", "--section", "artifacts", "--pull"]) == 0
    assert "pull" in inspector.instances[-1].calls
    assert "pull completed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "flags",
    [
        ["--pull"],
        ["--checkout"],
        ["--files"],
        ["--path", "x"],
        ["--section", "sources", "--pull"],
        ["--section", "artifacts", "--checkout"],
        ["--section", "sources", "--path", "../escape"],
        ["--section", "artifacts", "--path", "/absolute"],
    ],
)
def test_invalid_inspection_flags_fail_before_commands(inspector, capsys, flags):
    assert main(["inspect", "app", *flags]) == 2
    assert not capsys.readouterr().out and not inspector.instances


def test_headless_commands_never_import_textual_in_fresh_process(tmp_path):
    path = tmp_path / "graph.json"
    write_snapshot(graph(), path)
    source = Path(__file__).resolve().parents[1] / "src"
    script = """import importlib.abc,sys
class NoTUI(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,*args):
        if fullname.split('.')[0] == 'textual': raise AssertionError('Textual imported')
sys.meta_path.insert(0,NoTUI())
from bst_tree.cli import main
for command in ('tree','rdeps','paths'):
    args=[command]+(['lib'] if command!='tree' else [])+['--snapshot',sys.argv[1],'--format','json']
    assert main(args)==0
assert main(['inspect','--help']) is None
"""
    # argparse's help raises SystemExit(0), also with Textual unavailable.
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        env={**os.environ, "PYTHONPATH": str(source)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_snapshot_stdout_can_feed_headless_stdin(monkeypatch, capsys):
    import io

    monkeypatch.setattr("bst_tree.cli.capture", lambda *args, **kwargs: graph())
    assert main(["snapshot", "app", "-o", "-"]) == 0
    snapshot = capsys.readouterr().out
    assert Graph.from_dict(json.loads(snapshot)).targets == ["app", "other"]
    monkeypatch.setattr(sys, "stdin", io.StringIO(snapshot))
    assert main(["tree", "--snapshot", "-", "--scope", "build", "--format", "json"]) == 0
    assert "bootstrap" not in json.loads(capsys.readouterr().out)["graph"]["nodes"]


def test_real_inspector_checkout_is_removed_on_success_and_preview_error(tmp_path, monkeypatch, capsys):
    checkouts = []

    def command(self, arguments, **kwargs):
        if "%{source-info}" in arguments:
            return Execution(0, "[]\n")
        if "%{workspace-dirs}" in arguments:
            return Execution(0, "\n")
        if "checkout" in arguments:
            root = Path(arguments[arguments.index("--directory") + 1])
            root.mkdir()
            (root / ".hidden").write_text("content")
            (root / "build-import").write_text("#!/bin/sh\necho should-not-run\n")
            checkouts.append(root)
            return Execution(0)
        raise AssertionError(arguments)

    monkeypatch.setattr("bst_tools_core.process.CommandRunner.__call__", command)
    for path, expected in ((".hidden", 0), ("missing", 2)):
        assert (
            main(
                ["inspect", "app", "--section", "sources", "--checkout", "--files", "--path", path, "--format", "json"]
            )
            == expected
        )
        output = capsys.readouterr()
        assert not checkouts[-1].parent.exists()
        if expected == 0:
            data = json.loads(output.out)
            assert data["preview"]["text"] == "content"
            assert "build-import" in [f["path"] for f in data["files"]]
        else:
            assert not output.out and "missing" in output.err


@pytest.mark.parametrize(
    "content,limit,binary,truncated",
    [(b"abc", 3, False, False), (b"abcd", 3, False, True), (b"\x00x", 3, True, False)],
)
def test_structured_preview_flags(content, limit, binary, truncated):
    import io
    from bst_tree.inspection import preview_stream

    result = preview_stream(io.BytesIO(content), limit, structured=True)
    assert result["binary"] is binary and result["truncated"] is truncated
    assert result["text"] == (None if binary else content[:limit].decode())


def test_corrupt_archive_preview_is_a_clean_error(inspector, monkeypatch, capsys):
    import tarfile

    def broken(*args, **kwargs):
        raise tarfile.ReadError("corrupt export")

    monkeypatch.setattr(inspector, "preview_artifact", broken)
    assert main(["inspect", "app", "--section", "artifacts", "--path", "file", "--format", "json"]) == 2
    result = capsys.readouterr()
    assert not result.out and "corrupt export" in result.err
    assert inspector.instances[-1].cancelled


@pytest.mark.parametrize("command,element", [("tree", []), ("rdeps", ["lib"]), ("paths", ["lib"])])
def test_graph_options_may_precede_or_follow_live_targets(command, element, monkeypatch, capsys):
    calls = []

    def capture(targets, directory, options, **kwargs):
        calls.append((targets, directory, options))
        return graph()

    monkeypatch.setattr("bst_tree.headless.capture", capture)
    assert (
        main([command, *element, "-C", "/project", "app", "other", "--format", "json", "--option", "arch", "aarch64"])
        == 0
    )
    assert calls == [(["app", "other"], "/project", [["arch", "aarch64"]])]
    assert json.loads(capsys.readouterr().out)["query"] == command
