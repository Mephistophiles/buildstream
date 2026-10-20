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
"""Cross-tool contracts against real CAS blobs; a checkout call is a test failure."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from bst_cas import Index


@pytest.fixture
def project(cas, monkeypatch):
    text = cas.put(b"before\nneedle\nafter\n")
    root = cas.tree(files=[("data.txt", text, False)], links=[("outside", "/etc/passwd")])

    class Project:
        roots = {"one.bst": root, "two.bst": root}

        def __init__(self, *args, **kwargs):
            pass

        def report(self, names, fields, *args):
            return [
                {
                    field: {
                        "name": name,
                        "kind": "stack" if name == "product.bst" else "import",
                        "full-key": "key-" + name,
                        "artifact-cas-digest": self.roots.get(name, ""),
                    }[field]
                    for field in fields
                }
                for name in names
            ]

        def graph(self, *args):
            from bst_tools_core.project import Graph

            nodes = {
                n: {"kind": "import", "state": "cached", "full-key": "key-" + n, "build-deps": [], "runtime-deps": []}
                for n in self.roots
            }
            nodes["product.bst"] = {
                "kind": "stack",
                "state": "buildable",
                "full-key": "key-product.bst",
                "build-deps": [],
                "runtime-deps": list(self.roots),
            }
            return Graph(nodes, ["product.bst"], set(nodes), "all")

        def select(self, *args, **kwargs):
            return {n: "" for n in [*self.roots, "product.bst"]}

        def listings(self, *_):
            pytest.fail("CAS mode must not request text listings")

        def checkout_command(self, *_):
            pytest.fail("CAS mode must not export tar")

    return Project


def test_blame_and_grep_share_tree_without_listing_or_checkout(cas, database, project, monkeypatch, capsys):
    from bst_blame import cli as blame
    from bst_grep import cli as grep

    monkeypatch.setattr(blame, "Project", project)
    monkeypatch.setattr(blame.shutil, "which", lambda _: "bst")
    args = ["--cas-directory", str(cas.directory), "--cas-index", str(database), "--json"]
    assert blame.main([*args, "--compare-content", "/data.txt", "product.bst"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["content_comparison"] == "identical"
    assert {o["element"] for o in rows[0]["owners"]} == {"one.bst", "two.bst"}
    assert rows[-1]["coverage"]["cas_index"]["trees"] == 1
    assert rows[-1]["coverage"]["cas_index"]["identity_queries"] == 2
    assert rows[-1]["coverage"]["cas_index"]["shared_tree_reuses"] == 1
    # Ripgrep uses real execution; only bst availability was stubbed above.
    if shutil.which("rg") is None:
        pytest.skip("Install ripgrep")
    monkeypatch.setattr(grep, "Project", project)
    assert grep.main([*args, "--in", "artifacts", "-g", "*.txt", "needle", "product.bst"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    matches = [r for r in rows if r["type"] == "match"]
    assert [r["element"] for r in matches] == ["one.bst", "two.bst"]
    assert all(r["path"] == "data.txt" and r["line"] == 2 for r in matches)
    assert all(r["origin"].startswith("cas:") for r in matches)
    assert rows[-1]["cas_index"]["shared_tree_reuses"] == 1
    assert rows[-1]["skipped"] == ["product.bst"]
    # An indexed tree cannot hide missing bodies, even in ownership-only mode.
    cas.path(project.roots["one.bst"]).unlink()
    assert blame.main([*args, "/data.txt", "product.bst"]) == 2
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert not rows[-1]["coverage"]["complete"]


def test_elf_analysis_reuses_digest_cache_without_checkout(cas, database, project, monkeypatch, capsys):
    from bst_elf import cli as elf

    if not Path("/bin/true").exists():
        pytest.skip("Need an ELF fixture")
    binary = cas.put(Path("/bin/true").read_bytes())
    root = cas.tree(files=[("app", binary, True)])
    project.roots = {"one.bst": root}
    monkeypatch.setattr(elf, "Project", project)
    args = ["--cas-directory", str(cas.directory), "--cas-index", str(database), "--json", "product.bst"]
    assert elf.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["coverage"]["complete"]
    assert first["files"][0]["artifact_digest"] == root
    assert first["files"][0]["artifact_key"] == "key-one.bst"
    assert first["files"][0]["mode"] is None and first["files"][0]["executable"]
    assert first["coverage"]["cas_index"]["analysis_misses"] == 1
    monkeypatch.setattr(elf, "read_elf", lambda _: pytest.fail("unchanged ELF must use cached analysis"))
    assert elf.main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["files"] == first["files"]
    assert second["coverage"]["cas_index"]["analysis_hits"] == 1


def test_size_uses_digests_and_shared_tree_once(cas, database, project, monkeypatch, capsys):
    from bst_tree import size
    from bst_tree.cli import main
    from bst_tree.model import Graph

    graph = Graph(
        ["one.bst", "two.bst"],
        {n: {"kind": "import", "key": "key-" + n, "source_info": None, "workspace": False} for n in project.roots},
        {},
        {},
    )
    monkeypatch.setattr(size, "capture", lambda *args, **kwargs: graph)

    class Runner:
        def __init__(self, *_):
            pass

        def __call__(self, argv):
            assert "show" in argv and "checkout" not in argv
            names = argv[argv.index("--") + 1 :]
            template = argv[argv.index("--format") + 1]
            records = project().report(names, ("name", "full-key", "artifact-cas-digest"))
            output = ""
            for record in records:
                row = template
                for field, value in record.items():
                    row = row.replace("%{" + field + "}", value)
                output += row
            return subprocess.CompletedProcess(argv, 0, output)

        def cancel(self):
            pass

    monkeypatch.setattr(size, "CliRunner", Runner)
    args = [
        "size",
        "one.bst",
        "two.bst",
        "--format",
        "json",
        "--cas-directory",
        str(cas.directory),
        "--cas-index",
        str(database),
    ]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"]["file_bytes"] == 40
    assert report["total"]["content_unique_bytes"] == 20
    assert report["coverage"]["cas_index"]["trees"] == 1
    assert report["coverage"]["cas_index"]["shared_tree_reuses"] == 1


def test_wire_projection_matches_buildstream_proto(cas, database):
    native = pytest.importorskip("buildstream._protos.build.bazel.remote.execution.v2.remote_execution_pb2")
    body = cas.put(b"data")
    directory = native.Directory()
    file = directory.files.add(name="file", is_executable=True)
    file.digest.hash, size = body.split("/")
    file.digest.size_bytes = int(size)
    file.node_properties.mtime.seconds = 100
    file.node_properties.unix_mode.value = 0o755
    root = cas.put(directory.SerializeToString())
    with Index(cas.directory, database) as index:
        assert index.tree(root)[0]["digest"] == body
