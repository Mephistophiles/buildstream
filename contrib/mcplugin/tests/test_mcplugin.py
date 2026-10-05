# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Protocol, navigation and live CLI contracts for the MC plugin."""

import io
import json
import os
from pathlib import Path
import posixpath
import tarfile
from types import SimpleNamespace

import pytest

from bst_mc import cli, vfs
from bst_tree.model import Graph


@pytest.mark.parametrize("exit_code", [0, 1])
def test_stderr_only_on_failure(monkeypatch, capsys, exit_code):
    def run(args, **kwargs):
        assert kwargs["stderr"] == vfs.subprocess.PIPE
        return SimpleNamespace(returncode=exit_code, stdout="payload", stderr="bst diagnostics\n")

    monkeypatch.setattr(vfs.subprocess, "run", run)
    assert vfs.run_bst(["bst"], text=True).stdout == "payload"
    assert capsys.readouterr().err == ("bst diagnostics\n" if exit_code else "")


@pytest.fixture
def descriptor(tmp_path):
    return {"format_version": 1, "directory": str(tmp_path), "targets": ["app.bst"],
            "options": [["arch", "aarch64"]]}


@pytest.fixture
def graph():
    nodes = {name: {"kind": "manual", "key": None, "source_info": [], "workspace": False}
             for name in ("app.bst", "sdk.bst:base/lib.bst", "compiler.bst", "shared.bst")}
    return Graph(["app.bst"], nodes, {
        ("app.bst", "sdk.bst:base/lib.bst"): frozenset({"run"}),
        ("app.bst", "compiler.bst"): frozenset({"build"}),
        ("sdk.bst:base/lib.bst", "shared.bst"): frozenset({"run"}),
        ("compiler.bst", "shared.bst"): frozenset({"run"}),
        ("shared.bst", "app.bst"): frozenset({"build"}),
    }, {})


def test_finite_graph_navigation_and_scopes(descriptor, graph):
    project = vfs.Project(descriptor)
    project._graph = graph
    entries = {entry.path: entry for entry in project.entries()}
    assert "run/elements/compiler.bst" not in entries
    assert "build/elements/sdk.bst%3Abase%2Flib.bst" not in entries
    assert "build/elements/shared.bst" in entries
    for entry in entries.values():
        if entry.kind == "link":
            target = posixpath.normpath(posixpath.join(posixpath.dirname(entry.path), entry.target))
            assert target in entries
            assert entries[target].kind == "dir"
    assert len(entries) < 200
    assert "[see above]" in project.tree("all")
    assert len(project.tree("all").splitlines()) == len(graph.edges) + len(graph.targets)
    assert "[build]" in project.tree("all")
    assert "[run]" in project.tree("all")


def test_list_does_not_inspect_or_export(descriptor, graph, monkeypatch):
    calls = []
    monkeypatch.setattr(vfs, "capture", lambda *args, **kwargs: calls.append(args) or graph)
    project = vfs.Project(descriptor, run=lambda *args, **kwargs: pytest.fail("Unexpected inspection"))
    assert list(project.entries())
    assert len(calls) == 1
    assert calls[0] == (["app.bst"], descriptor["directory"], [["arch", "aarch64"]])


@pytest.mark.parametrize("action,expected", [
    ("source-info.txt", ["show", "--deps", "none", "--format", "%{source-info}", "--", "sdk.bst:base/lib.bst"]),
    ("build-commands.txt", ["show", "--deps", "none", "--format",
                            "Configuration:\n%{config}\nVariables:\n%{vars}\nEnvironment:\n%{env}",
                            "--", "sdk.bst:base/lib.bst"]),
    ("artifact-list.txt", ["artifact", "list-contents", "--long", "--", "sdk.bst:base/lib.bst"]),
])
def test_lazy_inspection_commands(descriptor, tmp_path, action, expected):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="file contents\n")

    project = vfs.Project(descriptor, run=run)
    output = tmp_path / "out"
    project.copyout("all/elements/sdk.bst%3Abase%2Flib.bst/" + action, output)
    assert output.read_text() == "file contents\n"
    assert calls == [["bst", "--no-colors", "--strict", "-C", descriptor["directory"],
                      "--option", "arch", "aarch64", *expected]]


@pytest.mark.parametrize("action", ["sources.tar", "artifact.tar"])
@pytest.mark.parametrize("failure", [False, True, "cancel"])
def test_exports_and_cleanup(descriptor, tmp_path, action, failure):
    exports = []

    def run(args, **kwargs):
        assert args[-2:] == ["--", "app.bst"]
        assert args[args.index("--deps") + 1] == "none"
        assert ("--no-integrate" in args) == (action == "artifact.tar")
        assert "--hardlinks" not in args
        archive = Path(args[args.index("--tar") + 1])
        assert not archive.exists()
        exports.append(archive)
        if failure == "cancel":
            raise KeyboardInterrupt
        if failure:
            archive.write_bytes(b"partial")
            return SimpleNamespace(returncode=1, stdout="")
        with tarfile.open(archive, "w") as stream:
            info = tarfile.TarInfo("dir/file with spaces.bin")
            info.size = 5
            stream.addfile(info, io.BytesIO(b"a\x00b\xffc"))
        return SimpleNamespace(returncode=0, stdout="diagnostics must not replace tar bytes")

    project = vfs.Project(descriptor, run=run)
    output = tmp_path / "out"
    output.write_bytes(b"original")  # MC creates the destination before calling copyout.
    if failure:
        with pytest.raises(KeyboardInterrupt if failure == "cancel" else ValueError):
            project.copyout("all/elements/app.bst/" + action, output)
        assert output.read_bytes() == b"original"
    else:
        project.copyout("all/elements/app.bst/" + action, output)
        with tarfile.open(output) as stream:
            assert stream.extractfile("dir/file with spaces.bin").read() == b"a\x00b\xffc"
    assert len(exports) == 1
    assert not exports[0].parent.exists()


def test_paths_and_metadata(descriptor, graph, tmp_path):
    project = vfs.Project(descriptor)
    project._graph = graph
    output = tmp_path / "out"
    project.copyout("run/elements/shared.bst/paths.txt", output)
    assert output.read_text() == "app.bst -> sdk.bst:base/lib.bst -> shared.bst\n"
    project.copyout("all/elements/app.bst/element.json", output)
    assert json.loads(output.read_text())["name"] == "app.bst"


@pytest.mark.parametrize("member", ["../etc/passwd", "/README.txt", "all/elements/app.bst/../../x",
                                    "all/elements/app.bst/unknown", "all/elements/a%2fb/source-info.txt"])
def test_invalid_members(descriptor, tmp_path, member):
    project = vfs.Project(descriptor, run=lambda *a, **kw: pytest.fail("Invalid member reached bst"))
    with pytest.raises(ValueError):
        project.copyout(member, tmp_path / "out")


@pytest.mark.parametrize("change", [{"format_version": True}, {"format_version": 2}, {"directory": "."},
                                   {"targets": "app.bst"}, {"options": ["--foo"]}])
def test_invalid_bookmarks(descriptor, change):
    with pytest.raises(ValueError):
        vfs.Project({**descriptor, **change})


@pytest.mark.parametrize("command", ["copyin", "rm", "mkdir", "rmdir", "run"])
def test_read_only(command, capsys):
    assert vfs.main([command, "missing.bstmc", "file"]) == 1
    assert "Read-only" in capsys.readouterr().err


def test_protocol_and_cli(tmp_path, descriptor, graph, monkeypatch, capsys):
    bookmark = tmp_path / "project.bstmc"
    assert cli.main(["-C", str(tmp_path), "--option", "arch", "aarch64", "-o", str(bookmark), "app.bst"]) == 0
    assert json.loads(bookmark.read_text()) == descriptor
    assert cli.main(["-o", str(bookmark), "other.bst"]) == 1
    assert json.loads(bookmark.read_text()) == descriptor
    capsys.readouterr()
    monkeypatch.setattr(vfs, "capture", lambda *a, **kw: graph)
    assert vfs.main(["list", str(bookmark)]) == 0
    listing = capsys.readouterr().out
    assert "lrwxrwxrwx 1 0 0 0 01-01-2000 00:00 all/targets/app.bst -> ../elements/app.bst" in listing
    output = tmp_path / "out"
    assert vfs.main(["copyout", str(bookmark), "README.txt", str(output)]) == 0
    assert output.read_text() == vfs.HELP


def test_failed_listing_emits_no_partial_archive(tmp_path, descriptor, monkeypatch, capsys):
    bookmark = tmp_path / "project.bstmc"
    bookmark.write_text(json.dumps(descriptor))

    def capture(*args, **kwargs):
        raise ValueError("Cannot load project")

    monkeypatch.setattr(vfs, "capture", capture)
    assert vfs.main(["list", str(bookmark)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "Cannot load project" in output.err


def test_temporary_launcher_cleanup(tmp_path, monkeypatch):
    paths = []

    def launch(args):
        assert args[0] == "mc"
        path = Path(args[1].removesuffix("/bstmc://"))
        assert json.loads(path.read_text())["directory"] == str(tmp_path)
        paths.append(path)
        return 0

    monkeypatch.setattr(cli.subprocess, "call", launch)
    assert cli.main(["-C", str(tmp_path), "app.bst"]) == 0
    assert not paths[0].parent.exists()


@pytest.mark.skipif(os.environ.get("BST_MC_TEST_LIVE") != "1", reason="Requires bst and buildbox-casd")
def test_live_buildstream(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    project_dir = tmp_path / "project with spaces"
    elements = project_dir / "elements"
    elements.mkdir(parents=True)
    (project_dir / "project.conf").write_text(
        "name: mc-test\nmin-version: '2.0'\nelement-path: elements\n"
        "defaults:\n  targets: [app.bst]\nsandbox:\n  build-os: linux\n  build-arch: aarch64\n"
    )
    (elements / "app.bst").write_text("kind: manual\ndepends: [files.bst]\nconfig:\n  build-commands: [echo hello]\n")
    (elements / "files.bst").write_text("kind: import\nsources:\n- kind: local\n  path: source\n")
    source = project_dir / "source"
    source.mkdir()
    (source / "hello world.txt").write_text("Hello from MC!\n")
    (source / "binary").write_bytes(b"\x00\xff\x01")
    (source / "link").symlink_to("hello world.txt")
    project = vfs.Project({"format_version": 1, "directory": str(project_dir), "targets": [], "options": []})
    assert project.graph.targets == ["app.bst"]
    assert "all/targets/app.bst" in {entry.path for entry in project.entries()}
    output = tmp_path / "out"
    project.copyout("all/elements/app.bst/build-commands.txt", output)
    assert "echo hello" in output.read_text()
    project.copyout("all/elements/files.bst/source-info.txt", output)
    assert output.read_text().strip()
    project.copyout("all/elements/files.bst/sources.tar", output)
    with tarfile.open(output) as stream:
        member = next(item for item in stream.getmembers() if item.name.endswith("hello world.txt"))
        assert stream.extractfile(member).read() == b"Hello from MC!\n"
    with pytest.raises(ValueError):
        project.copyout("all/elements/files.bst/artifact.tar", output)
    project.inspector.execute(["build", "--", "files.bst"])
    project.copyout("all/elements/files.bst/artifact-list.txt", output)
    assert "hello world.txt" in output.read_text()
    project.copyout("all/elements/files.bst/artifact.tar", output)
    with tarfile.open(output) as stream:
        member = next(item for item in stream.getmembers() if item.name.endswith("hello world.txt"))
        assert stream.extractfile(member).read() == b"Hello from MC!\n"
        binary = next(item for item in stream.getmembers() if item.name.endswith("binary"))
        assert stream.extractfile(binary).read() == b"\x00\xff\x01"
        assert any(item.issym() for item in stream.getmembers())
