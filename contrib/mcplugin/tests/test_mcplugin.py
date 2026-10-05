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
from bst_mc._graph import Graph


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
    assert "build/elements/sdk.bst [junction]/base/lib.bst" not in entries
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


def test_nested_element_directories_and_relative_links(descriptor, graph):
    names = ["default/element/fo/bar.bst", "default/element/fo/baz.bst", "other/bar.bst",
             "sdk.bst:base/dir with spaces/lib.bst"]
    graph = Graph(names[:2], {name: next(iter(graph.nodes.values())) for name in names}, {
        (names[0], names[2]): frozenset({"build", "run"}),
        (names[0], names[3]): frozenset({"run"}),
        (names[1], names[2]): frozenset({"run"}),
    }, {})
    project = vfs.Project(descriptor)
    project._graph = graph
    entries = list(project.entries())
    indexed = {entry.path: entry for entry in entries}
    assert len(indexed) == len(entries)
    assert "all/elements/default/element/fo/bar.bst" in indexed
    assert "all/targets/default/element/fo/bar.bst" in indexed
    assert "all/elements/default/element/fo/bar.bst/dependencies/other/bar.bst [build+run]" in indexed
    assert "all/elements/other/bar.bst/reverse-dependencies/default/element/fo/baz.bst [run]" in indexed
    assert "all/elements/sdk.bst [junction]/base/dir%20with%20spaces/lib.bst" in indexed
    seen = set()
    for entry in entries:
        assert "%2F" not in entry.path
        parent = posixpath.dirname(entry.path)
        assert not parent or parent in seen
        if entry.kind == "link":
            target = posixpath.normpath(posixpath.join(parent, entry.target))
            assert indexed[target].kind == "dir"
        seen.add(entry.path)


@pytest.mark.parametrize("name,path", [
    ("sdk.bst:base/lib.bst", "sdk.bst [junction]/base/lib.bst"),
    ("sdk.bst::base/lib.bst", "sdk.bst [junction x2]/base/lib.bst"),
    ("junctions/sdk.bst:ports/base.bst:default/lib.bst",
     "junctions/sdk.bst [junction]/ports/base.bst [junction]/default/lib.bst"),
    ("sdk.bst:base.bst::app.bst", "sdk.bst [junction]/base.bst [junction x2]/app.bst"),
    ("sdk.bst [junction]/base/lib.bst", "sdk.bst%20%5Bjunction%5D/base/lib.bst"),
    ("sdk.bst%3Abase/lib.bst", "sdk.bst%253Abase/lib.bst"),
])
def test_junction_paths_preserve_exact_names(name, path):
    assert vfs.element_path(name) == path
    assert vfs.element_name(path) == name


def test_junction_navigation_links_and_listing(descriptor, graph, tmp_path):
    names = ["sdk.bst:ports/base.bst:lib.bst", "sdk.bst::ports/base.bst:lib.bst",
             "sdk.bst/ports/base.bst/lib.bst", "sdk.bst [junction]/ports/base.bst/lib.bst"]
    graph = Graph(names, {name: next(iter(graph.nodes.values())) for name in names}, {
        (names[0], names[1]): frozenset({"build"}),
        (names[1], names[0]): frozenset({"run"}),
    }, {})
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="listing\n")

    project = vfs.Project(descriptor, run=run)
    project._graph = graph
    entries = list(project.entries())
    indexed = {entry.path: entry for entry in entries}
    assert len(entries) == len(indexed)
    for name in names:
        assert f"all/targets/{vfs.element_path(name)}" in indexed
        project.copyout(f"all/elements/{vfs.element_path(name)}/element.json", tmp_path / "out")
        assert json.loads((tmp_path / "out").read_text())["name"] == name
        project.copyout(f"all/elements/{vfs.element_path(name)}/artifact-list.txt", tmp_path / "out")
        assert calls[-1][-5:] == ["artifact", "list-contents", "--long", "--", name]
    for entry in entries:
        if entry.kind == "link":
            target = posixpath.normpath(posixpath.join(posixpath.dirname(entry.path), entry.target))
            assert indexed[target].kind == "dir"


@pytest.mark.parametrize("path", [
    "sdk.bst%3Abase/lib.bst", "sdk.bst [junction]", "[junction]/lib.bst",
    "sdk.bst [junction]/%2E%2E/lib.bst", "sdk.bst [junction x3]/lib.bst",
])
def test_noncanonical_junction_paths_are_rejected(path):
    with pytest.raises(ValueError):
        vfs.element_name(path)


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
    project.copyout("all/elements/sdk.bst [junction]/base/lib.bst/" + action, output)
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
        if "show" in args:
            return SimpleNamespace(returncode=0, stdout="cached\n")
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


@pytest.mark.parametrize("state", ["cached", "failed", "fetch needed", "buildable", "waiting"])
def test_artifact_export_pulls_only_when_missing(descriptor, tmp_path, state):
    calls = []
    name = "sdk.bst:base/lib.bst"
    project = vfs.Project(descriptor)

    def run(args, **kwargs):
        command = args[len(project.inspector.base):]
        calls.append(command)
        if command[0] == "show":
            return SimpleNamespace(returncode=0, stdout=state + "\n")
        if command[:2] == ["artifact", "checkout"]:
            Path(command[command.index("--tar") + 1]).write_bytes(b"artifact contents")
        return SimpleNamespace(returncode=0, stdout="")

    project.inspector.run = run
    output = tmp_path / "out"
    project.copyout("all/elements/sdk.bst [junction]/base/lib.bst/artifact.tar", output)
    assert output.read_bytes() == b"artifact contents"
    expected = [["show", "--deps", "none", "--format", "%{state}", "--", name]]
    if state not in ("cached", "failed"):
        expected.append(["artifact", "pull", "--deps", "none", "--", name])
    assert calls[:-1] == expected
    assert calls[-1][:5] == ["artifact", "checkout", "--deps", "none", "--no-integrate"]
    assert calls[-1][-2:] == ["--", name]


@pytest.mark.parametrize("failure", ["show", "pull", "cancel"])
def test_artifact_export_pull_failure_preserves_destination(descriptor, tmp_path, failure):
    calls = []

    def run(args, **kwargs):
        command = "show" if "show" in args else "pull"
        assert "checkout" not in args
        calls.append(command)
        if command == "pull" and failure == "cancel":
            raise KeyboardInterrupt
        return SimpleNamespace(returncode=int(command == failure), stdout="fetch needed\n")

    project = vfs.Project(descriptor, run=run)
    output = tmp_path / "out"
    output.write_bytes(b"original")
    with pytest.raises(KeyboardInterrupt if failure == "cancel" else ValueError):
        project.copyout("all/elements/app.bst/artifact.tar", output)
    assert output.read_bytes() == b"original"
    assert calls == (["show"] if failure == "show" else ["show", "pull"])


def test_paths_and_metadata(descriptor, graph, tmp_path):
    project = vfs.Project(descriptor)
    project._graph = graph
    output = tmp_path / "out"
    project.copyout("run/elements/shared.bst/paths.txt", output)
    assert output.read_text() == "app.bst -> sdk.bst:base/lib.bst -> shared.bst\n"
    project.copyout("all/elements/app.bst/element.json", output)
    assert json.loads(output.read_text())["name"] == "app.bst"


@pytest.mark.parametrize("member", ["../etc/passwd", "/README.txt", "all/elements/app.bst/../../x",
                                    "all/elements/app.bst/unknown", "all/elements/a%2fb/source-info.txt",
                                    "all/elements/a%2Fb/source-info.txt",
                                    "all/elements/%2E%2E/app.bst/source-info.txt"])
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
    (tmp_path / "project.conf").write_text("name: test\n")
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
    (tmp_path / "project.conf").write_text("name: test\n")
    monkeypatch.setattr(cli, "check_helper", lambda: tmp_path / "bstmc")
    monkeypatch.setattr(cli, "check_project", lambda *args: None)

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
    (elements / "default/element/fo").mkdir(parents=True)
    (project_dir / "project.conf").write_text(
        "name: mc-test\nmin-version: '2.0'\nelement-path: elements\n"
        "defaults:\n  targets: [app.bst]\nsandbox:\n  build-os: linux\n  build-arch: aarch64\n"
    )
    (elements / "app.bst").write_text("kind: manual\ndepends: [default/element/fo/files.bst]\nconfig:\n  build-commands: [echo hello]\n")
    (elements / "default/element/fo/files.bst").write_text("kind: import\nsources:\n- kind: local\n  path: source\n")
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
    project.copyout("all/elements/default/element/fo/files.bst/source-info.txt", output)
    assert output.read_text().strip()
    project.copyout("all/elements/default/element/fo/files.bst/sources.tar", output)
    with tarfile.open(output) as stream:
        member = next(item for item in stream.getmembers() if item.name.endswith("hello world.txt"))
        assert stream.extractfile(member).read() == b"Hello from MC!\n"
    with pytest.raises(ValueError):
        project.copyout("all/elements/default/element/fo/files.bst/artifact.tar", output)
    project.inspector.execute(["build", "--", "default/element/fo/files.bst"])
    project.copyout("all/elements/default/element/fo/files.bst/artifact-list.txt", output)
    assert "hello world.txt" in output.read_text()
    project.copyout("all/elements/default/element/fo/files.bst/artifact.tar", output)
    with tarfile.open(output) as stream:
        member = next(item for item in stream.getmembers() if item.name.endswith("hello world.txt"))
        assert stream.extractfile(member).read() == b"Hello from MC!\n"
        binary = next(item for item in stream.getmembers() if item.name.endswith("binary"))
        assert stream.extractfile(binary).read() == b"\x00\xff\x01"
        assert any(item.issym() for item in stream.getmembers())

    # Only the test server uses an internal API; the plugin still invokes bst.
    from buildstream._cas.casserver import create_server

    remote = tmp_path / "remote"
    remote.mkdir()
    with create_server(str(remote), enable_push=True, quota=None, index_only=False) as server:
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            url = f"http://127.0.0.1:{port}"
            project.inspector.execute(["artifact", "push", "--artifact-remote", url, "--",
                                       "default/element/fo/files.bst"])
            monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "fresh-cache"))
            config = tmp_path / "config" / "buildstream.conf"
            config.parent.mkdir(exist_ok=True)
            config.write_text(f"artifacts:\n  servers:\n  - url: {url}\n")
            with pytest.raises(ValueError):
                project.copyout("all/elements/default/element/fo/files.bst/artifact-list.txt", output)
            # Opening the archive must pull the missing artifact from the remote.
            project.copyout("all/elements/default/element/fo/files.bst/artifact.tar", output)
            with tarfile.open(output) as stream:
                member = next(item for item in stream.getmembers() if item.name.endswith("hello world.txt"))
                assert stream.extractfile(member).read() == b"Hello from MC!\n"
            project.copyout("all/elements/default/element/fo/files.bst/artifact-list.txt", output)
            assert "hello world.txt" in output.read_text()
        finally:
            server.stop(0).wait()


@pytest.mark.skipif(os.environ.get("BST_MC_TEST_LIVE") != "1", reason="Requires bst and buildbox-casd")
def test_live_nested_junction(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "project"
    for path, name in ((root, "main"), (root / "sdk", "sdk"), (root / "sdk/base", "base")):
        (path / "elements").mkdir(parents=True)
        (path / "project.conf").write_text(
            f"name: {name}\nmin-version: '2.0'\nelement-path: elements\n"
            "sandbox:\n  build-os: linux\n  build-arch: aarch64\n"
        )
    # Junctions load in the first pass, before project sandbox defaults apply.
    junction = "kind: junction\nsandbox:\n  build-os: linux\n  build-arch: aarch64\nsources:\n- kind: local\n"
    (root / "elements/sdk.bst").write_text(junction + "  path: sdk\n")
    (root / "sdk/elements/base.bst").write_text(junction + "  path: base\n")
    (root / "sdk/base/elements/lib.bst").write_text("kind: manual\nconfig:\n  build-commands: [echo nested-junction]\n")
    name = "sdk.bst:base.bst:lib.bst"
    project = vfs.Project({"format_version": 1, "directory": str(root), "targets": [name], "options": []})
    path = "sdk.bst [junction]/base.bst [junction]/lib.bst"
    entries = {entry.path: entry for entry in project.entries()}
    assert f"all/targets/{path}" in entries
    output = tmp_path / "out"
    project.copyout(f"all/elements/{path}/build-commands.txt", output)
    assert "echo nested-junction" in output.read_text()
    project.copyout(f"all/elements/{path}/element.json", output)
    assert json.loads(output.read_text())["name"] == name
