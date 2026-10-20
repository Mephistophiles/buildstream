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
import os
import pytest


@pytest.mark.skipif(
    os.environ.get("BST_DIFF_TEST_LIVE") != "1", reason="Set BST_DIFF_TEST_LIVE=1 with bst/buildbox installed"
)
def test_live_artifact_comparison(tmp_path, monkeypatch, capsys):
    from bst_diff.artifacts import ArtifactComparison
    from bst_tools_core.project import command_base
    from bst_tools_core.process import execute

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    elements = project / "elements"
    elements.mkdir(parents=True)
    (project / "project.conf").write_text(
        "name: artifact-test\nmin-version: '2.0'\nelement-path: elements\n"
        "sandbox:\n  build-os: linux\n  build-arch: aarch64\n"
    )
    source = project / "source"
    source.mkdir()
    config = source / "config"
    config.write_text("old\n")
    config.chmod(0o644)
    (source / "link").symlink_to("config")
    (elements / "import.bst").write_text("kind: import\nsources:\n- kind: local\n  path: source\n")
    base = command_base(project)
    execute(base + ["build", "--", "import.bst"])
    key = execute(base + ["show", "--deps", "none", "--format", "%{full-key}", "--", "import.bst"]).strip()
    assert len(key) == 64, key
    old_ref = "artifact-test/import/" + key
    config.write_text("new\n")
    config.chmod(0o755)
    (source / "link").unlink()
    (source / "link").symlink_to("missing")
    (source / "added").write_text("new file\n")
    execute(base + ["build", "--", "import.bst"])
    session = ArtifactComparison(old_ref, "import.bst", directory=project)
    try:
        session.load()
        records = {r["path"]: r for r in session.records}
        assert records["config"]["fields"] == ["mode", "content"]
        assert records["link"]["fields"] == ["target"]
        assert records["added"]["change"] == "added"
        assert "-old" in session.text_diff("config")
        assert "+new" in session.text_diff("config")
    finally:
        session.close()
    assert not session.root.exists()
