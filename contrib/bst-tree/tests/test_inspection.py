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
            output = "app.bst:\n  /usr/bin/app\n"
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
    assert calls[1][8:12] == ["show", "--deps", "none", "--format"]
    assert all(field in calls[1][12] for field in ("%{config}", "%{vars}", "%{env}"))


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
