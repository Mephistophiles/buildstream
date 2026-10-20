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
"""Exercise public bst identities and real buildbox CAS with all consumers."""

import json
import os
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("BST_CAS_TEST_LIVE") != "1", reason="Set BST_CAS_TEST_LIVE=1 with bst/buildbox, gcc and rg"
)


def test_real_buildstream_cas_shared_between_tools(tmp_path, monkeypatch, capsys):
    from bst_elf.cli import main as elf
    from bst_blame.cli import main as blame
    from bst_grep.cli import main as grep
    from bst_tree.cli import main as tree
    from bst_cas import Index

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    (project / "elements").mkdir(parents=True)
    (project / "project.conf").write_text(
        "name: cas-live\nmin-version: '2.0'\nelement-path: elements\nsandbox:\n  build-os: linux\n  build-arch: aarch64\n"
    )
    source = project / "source"
    source.mkdir()
    (source / "config.txt").write_text("needle\n")
    code = tmp_path / "demo.c"
    code.write_text("int answer(void) { return 42; }\n")
    subprocess.run(
        [
            "gcc",
            "-shared",
            "-nostdlib",
            "-fPIC",
            "-Wl,-soname,libdemo.so",
            str(code),
            "-o",
            str(source / "libdemo.so"),
        ],
        check=True,
        capture_output=True,
    )
    (source / "alias.so").symlink_to("libdemo.so")
    for name in ("one", "two"):
        (project / "elements" / (name + ".bst")).write_text("kind: import\nsources:\n- kind: local\n  path: source\n")
    (project / "elements" / "product.bst").write_text("kind: stack\ndepends:\n- one.bst\n- two.bst\n")
    subprocess.run(["bst", "-C", str(project), "build", "one.bst", "two.bst"], check=True, capture_output=True)
    cas = tmp_path / "cache" / "buildstream" / "cas"
    database = tmp_path / "shared.sqlite3"
    args = ["-C", str(project), "--cas-directory", str(cas), "--cas-index", str(database)]
    assert blame([*args, "--json", "--compare-content", "/config.txt", "product.bst"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["content_comparison"] == "identical"
    assert rows[-1]["coverage"]["cas_index"]["trees"] == 1
    assert rows[-1]["coverage"]["cas_index"]["identity_queries"] == 2
    assert grep([*args, "--in", "artifacts", "--deps", "run", "--json", "-g", "*.txt", "needle", "product.bst"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[-1]["matched_files"] == 2 and rows[-1]["complete"]
    assert rows[-1]["cas_index"]["hits"] == 1
    for iteration in range(2):
        assert elf([*args, "--deps", "none", "--json", "--check", "one.bst"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["coverage"]["complete"]
        assert report["files"][0]["elf"]["soname"] == "libdemo.so"
        if iteration:
            assert report["coverage"]["cas_index"]["analysis_misses"] == 0
            assert report["coverage"]["cas_index"]["analysis_hits"] == 2
    assert tree(["size", *args, "--format", "json", "product.bst"]) == 0
    report = json.loads(capsys.readouterr().out)
    expected = (source / "libdemo.so").stat().st_size + 7
    assert report["total"]["file_bytes"] == expected * 2
    assert report["total"]["content_unique_bytes"] == expected
    with Index(cas, database) as index:
        assert index.stats()["trees"] == 1
        assert index.stats()["analyses"] == 2

    from bst_cas.benchmark import measure
    from bst_tools_core.project import Project

    adapter = Project(project)
    keys = {
        r["name"].strip(): r["full-key"].strip() for r in adapter.report(["one.bst", "two.bst"], ("name", "full-key"))
    }
    benchmark = measure(adapter, cas, keys)
    print(json.dumps(benchmark, sort_keys=True))
    assert benchmark["equivalent"]
    assert [row["identity_queries"] for row in benchmark["measurements"]] == [4, 4, 2, 2]

    # A changed element must introduce a new digest without retaining a stale
    # owner binding. Existing content analyses remain reusable.
    (source / "config.txt").write_text("changed\n")
    subprocess.run(["bst", "-C", str(project), "build", "one.bst"], check=True, capture_output=True)
    assert grep([*args, "--in", "artifacts", "--json", "needle", "one.bst"]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["complete"]
    with Index(cas, database) as index:
        assert index.stats()["trees"] == 2
