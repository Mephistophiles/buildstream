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
import subprocess

import pytest

from bst_tree.inspection import ProjectInspector, preview_file


def test_live_inspection_commands(tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "list-contents" in args:
            output = "app.bst:\n\t" + ("-rwxr-xr-x exe 42 " if "--long" in args else "") + "/usr/bin/app\n"
        elif "%{workspace-dirs}" in args:
            output = f"Workspace: {tmp_path}\n"
        else:
            output = "resolved configuration or provenance"
        return subprocess.CompletedProcess(args, 0, output)

    inspector = ProjectInspector("/project with spaces", [("arch", "x86_64")], run=run)
    assert "/usr/bin/app" in inspector.load("sub.bst:app.bst", "artifacts").text
    assert "resolved configuration" in inspector.load("sub.bst:app.bst", "build").text
    assert inspector.load("sub.bst:app.bst", "sources").workspace == tmp_path.resolve()
    for call in calls:
        assert call[:7] == ["bst", "--no-colors", "--strict", "-C", "/project with spaces", "--option", "arch"]
        assert call[7] == "x86_64"
        assert call[-2:] == ["--", "sub.bst:app.bst"]
        assert not {"checkout", "fetch", "pull", "track"}.intersection(call)
    assert calls[0][8:-2] == ["artifact", "list-contents", "--long"]
    assert calls[2][8:12] == ["show", "--deps", "none", "--format"]
    assert all(field in calls[2][12] for field in ("%{config}", "%{vars}", "%{env}"))


def test_sources_without_workspace():
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "\n" if "%{workspace-dirs}" in args else "[]\n")

    result = ProjectInspector(run=run).load("empty.bst", "sources")
    assert result.workspace is None
    assert "No open workspace" in result.text


@pytest.mark.parametrize("output", ["%{workspace-dirs}\n", "not a workspace\n", "Workspace: /missing/bst-tree\n"])
def test_unavailable_workspace(output):
    inspector = ProjectInspector(run=lambda args, **kw: subprocess.CompletedProcess(args, 0, output))
    with pytest.raises(ValueError):
        inspector.load("app.bst", "sources")


def test_command_failure():
    inspector = ProjectInspector(run=lambda args, **kw: subprocess.CompletedProcess(args, 1, ""))
    with pytest.raises(ValueError, match="bst failed"):
        inspector.load("uncached.bst", "artifacts")


@pytest.mark.parametrize("failed", [False, True])
def test_artifact_pull_refreshes_listing_and_discards_preview(failed):
    from pathlib import Path
    import tempfile

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "pull" in args:
            return subprocess.CompletedProcess(args, int(failed), "")
        row = "-rw-r--r-- reg 3 fresh.txt" if "--long" in args else "fresh.txt"
        return subprocess.CompletedProcess(args, 0, "app.bst:\n\t" + row + "\n")

    inspector = ProjectInspector("/project with spaces", [("arch", "aarch64")], run=run)
    temporary = tempfile.TemporaryDirectory(prefix="bst-tree-test-preview-")
    archive = Path(temporary.name) / "artifact.tar"
    archive.write_bytes(b"old contents")
    inspector._artifact_previews["sdk.bst:dir/app.bst"] = (temporary, archive)
    try:
        if failed:
            with pytest.raises(ValueError):
                inspector.pull_artifact("sdk.bst:dir/app.bst")
            assert archive.exists()
            assert len(calls) == 1
        else:
            result = inspector.pull_artifact("sdk.bst:dir/app.bst")
            assert not archive.parent.exists()
            assert "Artifact pull completed" in result.text
            assert [entry.path for entry in result.artifacts] == ["fresh.txt"]
            assert "sdk.bst:dir/app.bst" not in inspector._artifact_previews
            assert len(calls) == 3
        assert calls[0] == inspector.base + ["artifact", "pull", "--deps", "none", "--", "sdk.bst:dir/app.bst"]
    finally:
        inspector.cancel()


def test_cancelled_artifact_pull_does_not_reload():
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        inspector.cancel()
        return subprocess.CompletedProcess(args, 0, "")

    inspector = ProjectInspector(run=run)
    with pytest.raises(ValueError, match="cancelled"):
        inspector.pull_artifact("app.bst")
    with pytest.raises(ValueError, match="cancelled"):
        inspector.pull_artifact("app.bst")
    assert len(calls) == 1


def test_file_previews(tmp_path):
    source = tmp_path / "main.c"
    source.write_text("int main() {}\n")
    assert preview_file(tmp_path, source) == "int main() {}\n"
    assert "truncated" in preview_file(tmp_path, source, limit=4)
    source.write_bytes(b"\x00\xff")
    assert "Binary file" in preview_file(tmp_path, source)
    with pytest.raises(ValueError, match="regular files"):
        preview_file(tmp_path, tmp_path)


def test_external_symlink_is_not_previewed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("not source code")
    link = workspace / "link"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        preview_file(workspace, link)


def test_artifact_names_and_metadata():
    paths = ["usr", "usr/a [b] -> c", "link with spaces"]
    rows = ["drwxr-xr-x dir 0 usr", "-rw-r--r-- reg 3 usr/a [b] -> c",
            "lrwxrwxrwx link 0 link with spaces -> usr/a [b] -> c"]

    def run(args, **kwargs):
        output = "  app.bst:\n\t" + "\n\t".join(rows if "--long" in args else paths) + "\n"
        return subprocess.CompletedProcess(args, 0, output)

    result = ProjectInspector(run=run).load("app.bst", "artifacts")
    assert [entry.path for entry in result.artifacts] == paths
    assert [entry.directory for entry in result.artifacts] == [True, False, False]
    assert result.artifacts[-1].details == rows[-1]


def test_source_checkout_cleanup_and_command():
    from pathlib import Path

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        path = Path(args[args.index("--directory") + 1])
        path.mkdir()
        (path / "hello.c").write_text("hello")
        return subprocess.CompletedProcess(args, 0, "")

    inspector = ProjectInspector("/project with spaces", [("arch", "aarch64")], run=run)
    result = inspector.checkout_sources("junction.bst:code.bst")
    assert preview_file(result.workspace, result.workspace / "hello.c") == "hello"
    assert calls[0][-8:] == ["source", "checkout", "--deps", "none", "--directory",
                            str(result.workspace), "--", "junction.bst:code.bst"]
    assert "'" in result.text  # Shell-quoted project path in the reproducible command.
    inspector.cancel()
    assert not result.workspace.parent.exists()
    inspector.cancel()


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_or_cancelled_checkout_removes_temporary_files(cancelled):
    from pathlib import Path

    checkout_paths = []

    def run(args, **kwargs):
        path = Path(args[args.index("--directory") + 1])
        path.mkdir()
        (path / "partial").write_text("partial checkout")
        checkout_paths.append(path)
        if cancelled:
            inspector.cancel()
        return subprocess.CompletedProcess(args, 0 if cancelled else 1, "")

    inspector = ProjectInspector(run=run)
    with pytest.raises(ValueError):
        inspector.checkout_sources("code.bst")
    assert not checkout_paths[0].parent.exists()


def test_artifact_previews_export_once_and_cleanup():
    import io
    from pathlib import Path
    import tarfile

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        archive = Path(args[args.index("--tar") + 1])
        with tarfile.open(archive, "w") as bundle:
            for name, data in [("./usr/a [b].txt", b"artifact contents"),
                               ("./binary", b"\x00\xff"), ("plain-name", b"plain")]:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                bundle.addfile(member, io.BytesIO(data))
            for name, kind in [("symlink", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE),
                               ("directory", tarfile.DIRTYPE), ("fifo", tarfile.FIFOTYPE)]:
                member = tarfile.TarInfo("./" + name)
                member.type = kind
                member.linkname = "/etc/passwd"
                bundle.addfile(member)
        return subprocess.CompletedProcess(args, 0, "")

    inspector = ProjectInspector("/project with spaces", [("arch", "aarch64")], run=run)
    assert inspector.preview_artifact("sub.bst:app.bst", "usr/a [b].txt") == "artifact contents"
    assert "truncated" in inspector.preview_artifact("sub.bst:app.bst", "usr/a [b].txt", limit=4)
    assert "Binary file" in inspector.preview_artifact("sub.bst:app.bst", "binary")
    assert inspector.preview_artifact("sub.bst:app.bst", "plain-name") == "plain"
    for name in ("symlink", "hardlink", "directory", "fifo"):
        with pytest.raises(ValueError, match="regular files"):
            inspector.preview_artifact("sub.bst:app.bst", name)
    with pytest.raises(ValueError, match="no longer"):
        inspector.preview_artifact("sub.bst:app.bst", "missing")
    for path in ("/etc/passwd", "../outside"):
        with pytest.raises(ValueError, match="outside"):
            inspector.preview_artifact("sub.bst:app.bst", path)
    assert len(calls) == 1
    command = calls[0]
    archive = Path(command[command.index("--tar") + 1])
    assert command[-9:] == ["artifact", "checkout", "--deps", "none", "--no-integrate",
                            "--tar", str(archive), "--", "sub.bst:app.bst"]
    assert command[:8] == inspector.base
    assert list(archive.parent.iterdir()) == [archive]  # No files extracted onto the host.
    inspector.cancel()
    assert not archive.parent.exists()
    with pytest.raises(ValueError, match="cancelled"):
        inspector.preview_artifact("sub.bst:app.bst", "plain-name")
    assert len(calls) == 1


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_artifact_preview_cleans_partial_archive(cancelled):
    from pathlib import Path

    archives = []

    def run(args, **kwargs):
        archive = Path(args[args.index("--tar") + 1])
        archive.write_bytes(b"partial export")
        archives.append(archive)
        if cancelled:
            inspector.cancel()
            assert archive.exists()  # Cleanup must wait until the command returns.
        return subprocess.CompletedProcess(args, 0 if cancelled else 1, "")

    inspector = ProjectInspector(run=run)
    with pytest.raises(ValueError):
        inspector.preview_artifact("app.bst", "file")
    assert not archives[0].parent.exists()
