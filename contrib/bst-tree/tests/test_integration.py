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
"""Live CLI contract test; Linux BuildStream wheels include buildbox-casd."""

import os

import pytest

from bst_tree.adapter import capture
from bst_tree.diff import compare
from bst_tree.model import read_snapshot, write_snapshot
from bst_tree.inspection import ProjectInspector, preview_file


@pytest.mark.skipif(
    os.environ.get("BST_TREE_TEST_LIVE") != "1", reason="Set BST_TREE_TEST_LIVE=1 with bst/buildbox installed"
)
def test_live_buildstream(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    elements = project / "elements"
    elements.mkdir(parents=True)
    # Fix the target platform: BuildStream 2.8.0 does not recognize Darwin's
    # 'arm64' host spelling, and these fixtures never execute target binaries.
    (project / "project.conf").write_text(
        "name: tree-test\nmin-version: '2.0'\nelement-path: elements\n"
        "sandbox:\n  build-os: linux\n  build-arch: aarch64\n"
    )
    (elements / "app.bst").write_text("kind: manual\ndepends:\n- lib.bst\n")
    (elements / "lib.bst").write_text("kind: stack\n")
    old = capture(["app.bst"], directory=project)
    assert old.targets == ["app.bst"]
    assert set(old.nodes) == {"app.bst", "lib.bst"}
    assert old.edges["app.bst", "lib.bst"] == {"build", "run"}
    write_snapshot(old, tmp_path / "before.json")
    assert read_snapshot(tmp_path / "before.json").as_dict() == old.as_dict()
    (elements / "app.bst").write_text("kind: manual\nbuild-depends:\n- lib.bst\n")
    new = capture(["app.bst"], directory=project)
    assert compare(old, new)["summary"]["edges_modified"] == 1

    # Inspect through the installed public CLI, including workspace display format.
    (project / "source").mkdir()
    (project / "source" / "main.c").write_text("int main() {}\n")
    (elements / "code.bst").write_text(
        "kind: manual\nsources:\n- kind: local\n  path: source\n"
        "config:\n  build-commands:\n  - echo hello\n"
    )
    inspector = ProjectInspector(project)
    assert "echo hello" in inspector.load("code.bst", "build").text
    assert inspector.load("code.bst", "sources").workspace is None
    workspace = tmp_path / "workspace"
    inspector.execute(["workspace", "open", "--directory", str(workspace), "--", "code.bst"])
    result = inspector.load("code.bst", "sources")
    assert result.workspace == workspace.resolve()
    assert preview_file(workspace, workspace / "main.c") == "int main() {}\n"
    with pytest.raises(ValueError):
        inspector.load("code.bst", "artifacts")

    # Import elements produce a real cached artifact without running a shell or
    # requiring a Linux build sandbox, so this also exercises macOS installations.
    (elements / "import.bst").write_text("kind: import\nsources:\n- kind: local\n  path: source\n")
    inspector.execute(["build", "--", "import.bst"])
    contents = inspector.load("import.bst", "artifacts").text
    assert "main.c" in contents
