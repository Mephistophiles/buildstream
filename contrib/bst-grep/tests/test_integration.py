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
"""Opt-in tests using real bst/buildbox-casd and ripgrep, without target execution."""

import json
import os
import subprocess

import pytest

from bst_grep.cli import main

pytestmark = pytest.mark.skipif(
    os.environ.get("BST_GREP_TEST_LIVE") != "1", reason="Set BST_GREP_TEST_LIVE=1 with bst/buildbox-casd and rg"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "project with spaces"
    elements = root / "elements"
    elements.mkdir(parents=True)
    (root / "project.conf").write_text(
        "name: grep-test\nmin-version: '2.0'\nelement-path: elements\n"
        "sandbox:\n  build-os: linux\n  build-arch: aarch64\n"
        "options:\n  feature:\n    type: bool\n    description: Test feature\n    default: false\n"
        "variables:\n  feature-flag: disabled\n"
        "(?):\n- feature:\n    variables:\n      feature-flag: FEATURE_NEEDLE\n"
    )
    source = root / "source"
    source.mkdir()
    (source / "main.c").write_text("before\nsource_needle\nafter\n")
    (source / ".hidden.h").write_text("source_needle\n")
    (elements / "code.bst").write_text(
        "kind: manual\nsources:\n- kind: local\n  path: source\n"
        "config:\n  build-commands:\n  - echo %{feature-flag}\n"
    )
    (elements / "app.bst").write_text("kind: stack\ndepends:\n- code.bst\n")
    (elements / "empty.bst").write_text("kind: stack\n")
    return root


def records(capsys):
    captured = capsys.readouterr()
    assert captured.err == ""
    return [json.loads(line) for line in captured.out.splitlines()]


def test_live_sources_config_options_workspaces_and_dependency_selection(project, tmp_path, capsys):
    common = ["-C", str(project), "--json"]
    assert main([*common, "source_needle", "code.bst"]) == 0
    rows = records(capsys)
    matches = [r for r in rows if r["type"] == "match"]
    assert {r["path"] for r in matches} == {"main.c", ".hidden.h"}
    assert {r["origin"] for r in matches} == {"checkout"}
    assert main([*common, "source_needle", "empty.bst"]) == 1
    assert records(capsys)[-1]["complete"] is True
    assert main([*common, "--deps", "all", "--why", "source_needle", "app.bst"]) == 0
    rows = records(capsys)
    assert rows[0]["dependency_paths"] == [["app.bst", "code.bst"]]
    assert rows[-1]["selected"] == 2
    # Selection follows bst: build dependencies exclude the target itself.
    assert main([*common, "--deps", "build", "source_needle", "code.bst"]) == 1
    assert records(capsys)[-1]["selected"] == 0
    assert main([*common, "--in", "config", "--option", "feature", "true", "FEATURE_NEEDLE", "code.bst"]) == 0
    rows = records(capsys)
    assert {r["path"] for r in rows if r["type"] == "match"} == {"config.yaml", "vars.yaml"}
    assert main([*common, "--in", "config", "FEATURE_NEEDLE", "code.bst"]) == 1
    records(capsys)

    workspace = tmp_path / "workspace"
    subprocess.run(
        ["bst", "-C", str(project), "workspace", "open", "--directory", str(workspace), "code.bst"],
        check=True,
        capture_output=True,
    )
    (workspace / "main.c").write_text("dirty_needle\n")
    assert main([*common, "dirty_needle", "code.bst"]) == 0
    rows = records(capsys)
    assert rows[0]["origin"] == "workspace"
    assert rows[0]["path"] == "main.c"
    assert (workspace / "main.c").read_text() == "dirty_needle\n"


def test_live_junction_paths_and_patched_sources(project, capsys):
    subproject = project / "subproject"
    elements = subproject / "elements" / "dir"
    elements.mkdir(parents=True)
    (subproject / "project.conf").write_text("name: sub\nmin-version: '2.0'\nelement-path: elements\n")
    (subproject / "code").mkdir()
    (subproject / "code" / "main.c").write_text("old_needle\n")
    (subproject / "change.patch").write_text("--- a/main.c\n+++ b/main.c\n@@ -1 +1 @@\n-old_needle\n+patched_needle\n")
    (elements / "code.bst").write_text(
        "kind: manual\nsources:\n- kind: local\n  path: code\n" "- kind: patch\n  path: change.patch\n"
    )
    (project / "elements" / "sdk.bst").write_text("kind: junction\nsources:\n- kind: local\n  path: subproject\n")
    common = ["-C", str(project), "--json", "-g", "*.c"]
    assert main([*common, "patched_needle", "sdk.bst:dir/code.bst"]) == 0
    rows = records(capsys)
    assert rows[0]["element"] == "sdk.bst:dir/code.bst"
    assert rows[0]["path"] == "main.c"
    assert main([*common, "old_needle", "sdk.bst:dir/code.bst"]) == 1
    assert records(capsys)[-1]["complete"] is True
