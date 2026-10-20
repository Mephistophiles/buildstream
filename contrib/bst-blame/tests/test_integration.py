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
"""Real cached import artifacts, without running target programs or build commands."""

import json
import os
import subprocess

import pytest

from bst_blame.cli import main

pytestmark = pytest.mark.skipif(
    os.environ.get("BST_BLAME_TEST_LIVE") != "1", reason="Set BST_BLAME_TEST_LIVE=1 with bst/buildbox-casd"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "project with spaces"
    (root / "elements").mkdir(parents=True)
    (root / "project.conf").write_text(
        "name: blame-test\nmin-version: '2.0'\nelement-path: elements\n"
        "sandbox:\n  build-os: linux\n  build-arch: aarch64\n"
    )
    for name, data in (("one", "alpha\n"), ("two", "bravo\n")):
        source = root / name / "usr" / "lib"
        source.mkdir(parents=True)
        (source / "libdemo.so").write_text(data)
        (source / "alias.so").symlink_to("libdemo.so")
        (root / "elements" / (name + ".bst")).write_text(
            "kind: import\nsources:\n- kind: local\n  path: " + name + "\n"
        )
    (root / "elements" / "app.bst").write_text("kind: stack\ndepends:\n- one.bst\n- two.bst\n")
    subprocess.run(["bst", "-C", str(root), "build", "one.bst", "two.bst"], check=True, capture_output=True)
    return root


def rows(capsys):
    captured = capsys.readouterr()
    assert captured.err == ""
    return [json.loads(row) for row in captured.out.splitlines()]


def test_real_local_owners_hashes_diffs_symlinks_and_missing_coverage(project, capsys):
    base = ["-C", str(project), "--json"]
    assert main([*base, "/usr/lib/libdemo.so", "app.bst"]) == 0
    result = rows(capsys)
    assert [o["element"] for o in result[0]["owners"]] == ["one.bst", "two.bst"]
    assert result[0]["metadata_equal"] is True
    assert result[0]["content_comparison"] == "not-compared"
    assert result[-1]["coverage"]["complete"] is True
    assert result[-1]["coverage"]["skipped"] == ["app.bst"]
    assert main([*base, "--diff", "/usr/lib/libdemo.so", "app.bst"]) == 0
    result = rows(capsys)
    assert result[0]["content_comparison"] == "different"
    assert "-alpha\n" in result[0]["diffs"][0]["lines"]
    assert "+bravo\n" in result[0]["diffs"][0]["lines"]
    assert main([*base, "/usr/lib/alias.so", "app.bst"]) == 0
    assert rows(capsys)[0]["content_comparison"] == "identical"
    assert main([*base, "--duplicates", "--glob", "/**", "app.bst"]) == 0
    result = rows(capsys)
    assert {r["path"] for r in result if r["type"] == "path"} == {"/usr/lib/alias.so", "/usr/lib/libdemo.so"}
    # A new unbuilt artifact makes coverage incomplete, without hiding owners.
    (project / "elements" / "missing.bst").write_text(
        "kind: import\nsources:\n- kind: local\n  path: one\nvariables:\n  extra-key: new\n"
    )
    (project / "elements" / "app.bst").write_text("kind: stack\ndepends:\n- one.bst\n- two.bst\n- missing.bst\n")
    assert main([*base, "/usr/lib/libdemo.so", "app.bst"]) == 2
    result = rows(capsys)
    assert len(result[0]["owners"]) == 2
    assert result[-1]["coverage"]["complete"] is False
    assert result[-1]["coverage"]["missing"][0]["element"] == "missing.bst"


def test_real_junction_elements_with_same_local_name(project, capsys):
    sub = project / "subproject"
    (sub / "elements").mkdir(parents=True)
    (sub / "project.conf").write_text("name: sub\nmin-version: '2.0'\nelement-path: elements\n")
    (sub / "source" / "usr" / "lib").mkdir(parents=True)
    (sub / "source" / "usr" / "lib" / "libdemo.so").write_text("other\n")
    (sub / "elements" / "one.bst").write_text("kind: import\nsources:\n- kind: local\n  path: source\n")
    (project / "elements" / "sdk.bst").write_text("kind: junction\nsources:\n- kind: local\n  path: subproject\n")
    (project / "elements" / "app.bst").write_text(
        "kind: stack\ndepends:\n- one.bst\n- filename: one.bst\n  junction: sdk.bst\n"
    )
    subprocess.run(["bst", "-C", str(project), "build", "sdk.bst:one.bst"], check=True, capture_output=True)
    assert main(["-C", str(project), "--json", "/usr/lib/libdemo.so", "app.bst"]) == 0
    result = rows(capsys)
    owners = {o["element"]: o for o in result[0]["owners"]}
    assert set(owners) == {"one.bst", "sdk.bst:one.bst"}
    assert owners["sdk.bst:one.bst"]["dependency_paths"] == [["app.bst", "sdk.bst:one.bst"]]
