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
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from bst_cas import Index, load_artifact
from bst_cas.cli import main
from bst_cas.index import digest_parts


def test_warm_cache_reuses_tree_and_analysis_but_not_owners(cas, database, monkeypatch):
    file = cas.put(b"payload")
    child = cas.tree(files=[("file", file, True)], links=[("alias", "../dir/file")])
    root = cas.tree(directories=[("dir", child)])
    with Index(cas.directory, database) as index:
        entries = index.tree(root)
        assert [e["path"] for e in entries] == ["/dir", "/dir/alias", "/dir/file"]
        assert entries[-1]["digest"] == file and entries[-1]["executable"]
        assert index.analyze(file, "test-v1", lambda f: f.read().decode()) == "payload"
        assert index.stats()["misses"] == 1
    with Index(cas.directory, database) as index:
        monkeypatch.setattr(index, "_walk", lambda _: pytest.fail("warm tree must not be parsed again"))
        assert index.tree(root) == entries
        assert index.analyze(file, "test-v1", lambda _: pytest.fail("analysis must be reused")) == "payload"
        assert index.stats()["hits"] == index.stats()["analysis_hits"] == 1
        # Parser version/options are a part of the analysis namespace.
        assert index.analyze(file, "test-v2", lambda _: None) is None
        assert index.analyze(file, "test-v2", lambda _: pytest.fail()) is None

        class Project:
            def report(self, names, fields):
                return [{"name": name, "full-key": name, "artifact-cas-digest": root} for name in names]

        for owner in ("a.bst", "junction.bst:b.bst"):
            rows, identity = load_artifact(index, Project(), owner, owner)
            assert rows == entries and identity["full-key"] == owner
        assert "owner" not in index.db.execute("SELECT payload FROM trees").fetchone()[0]


def test_changed_root_is_incremental_and_missing_or_corrupt_objects_fail(cas, database):
    file = cas.put(b"old")
    a = cas.tree(files=[("a", file, False)])
    b = cas.tree(files=[("b", file, False)])
    with Index(cas.directory, database) as index:
        assert index.tree(a)[0]["path"] == "/a"
        assert index.tree(b)[0]["path"] == "/b"
        cas.path(file).unlink()
        with pytest.raises(ValueError, match="unavailable"):
            index.tree(a)
        cas.put(b"old")
        with index.blob(file) as stream:
            assert stream.read() == b"old"
        cas.path(file).write_bytes(b"bad")  # Same size; stat signature changes.
        with pytest.raises(ValueError, match="checksum mismatch"):
            with index.blob(file):
                pass
        cas.path(a).write_bytes(b"x" * cas.path(a).stat().st_size)
        with pytest.raises(ValueError, match="checksum mismatch"):
            index.tree(a)


@pytest.mark.parametrize(
    "value", ["../oops/1", "A" * 64 + "/1", "f" * 64 + "/-1", "f" * 64 + "/01", "f" * 64 + "/9223372036854775808"]
)
def test_digest_validation(value):
    with pytest.raises(ValueError):
        digest_parts(value)


@pytest.mark.parametrize("name", ["../out", "/absolute", ".", "..", "", "bad\nname"])
def test_rejects_unsafe_directory_members(cas, database, name):
    root = cas.tree(files=[(name, cas.put(b""), False)])
    with Index(cas.directory, database) as index:
        with pytest.raises(ValueError, match="Invalid or duplicate"):
            index.tree(root)
        assert index.stats()["trees"] == 0


def test_duplicate_entries_invalid_proto_and_resource_limit(cas, database, monkeypatch):
    root = cas.tree(files=[("same", cas.put(b""), False)], links=[("same", "elsewhere")])
    with Index(cas.directory, database) as index:
        with pytest.raises(ValueError, match="duplicate"):
            index.tree(root)
        malformed = cas.put(b"\xff")
        with pytest.raises(ValueError, match="Malformed REAPI"):
            index.tree(malformed)
        monkeypatch.setattr("bst_cas.index.MAX_ENTRIES", 1)
        root = cas.tree(links=[("one", "/host"), ("two", "/etc/passwd")])
        with pytest.raises(ValueError, match="limit"):
            index.tree(root)
        assert index.stats()["trees"] == 0


def test_symlink_objects_not_followed_and_target_links_not_opened(cas, database, tmp_path):
    file = cas.put(b"secret")
    root = cas.tree(files=[("file", file, False)], links=[("host", "/etc/passwd")])
    target = tmp_path / "outside"
    cas.path(file).rename(target)
    cas.path(file).symlink_to(target)
    with Index(cas.directory, database) as index:
        with pytest.raises(ValueError, match="unavailable"):
            index.tree(root)
        assert index.stats()["trees"] == 0
        cas.path(file).unlink()
        cas.put(b"secret")
        assert index.tree(root)[1]["link_target"] == "/etc/passwd"


def test_rejects_identity_changes_and_corrupt_database(cas, database):
    root = cas.tree()

    class Project:
        calls = 0

        def report(self, names, *_):
            self.calls += 1
            return [{"name": name, "full-key": str(self.calls), "artifact-cas-digest": root} for name in names]

    with Index(cas.directory, database) as index:
        with pytest.raises(ValueError, match="Project key changed"):
            load_artifact(index, Project(), "app.bst", "wrong")
        with pytest.raises(ValueError, match="identity changed"):
            load_artifact(index, Project(), "app.bst")
        index.db.execute("UPDATE trees SET payload='{}'")
        with pytest.raises(ValueError, match="corrupt"):
            index.tree(root)
    database.write_bytes(b"not a database")
    with pytest.raises(ValueError, match="database"):
        Index(cas.directory, database)


def test_concurrent_readers_writers_and_prune_only_derived_records(cas, database):
    root = cas.tree(files=[("data", cas.put(b"content"), False)])
    with Index(cas.directory, database):
        pass

    def work(_):
        with Index(cas.directory, database) as index:
            return index.tree(root)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(work, range(8)))
    assert all(result == results[0] for result in results)
    with Index(cas.directory, database) as index:
        assert index.stats()["trees"] == 1
        assert index.prune(0)["trees"] == 1
        assert cas.path(root).exists()
        index.tree(root)


def test_cli_search_is_explicitly_historical_metadata(cas, database, capsys):
    root = cas.tree(links=[("link", "/outside")])
    args = ["--cas-directory", str(cas.directory), "--cas-index", str(database)]
    assert main([*args, "index", root]) == 0
    assert json.loads(capsys.readouterr().out)["entries"][0]["kind"] == "symlink"
    cas.path(root).unlink()
    assert main([*args, "find", "/*"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["scope"] == "indexed-metadata-only"
    assert report["entries"][0]["tree_digest"] == root
    assert main([*args, "index", root]) == 2
    assert not json.loads(capsys.readouterr().out)["complete"]


def test_analysis_failure_is_not_cached_and_cas_is_read_only(cas, database):
    body = cas.put(b"data")
    with Index(cas.directory, database) as index:

        def fail(stream):
            assert stream.read() == b"data"
            raise ValueError("failed parser")

        with pytest.raises(ValueError, match="failed parser"):
            index.analyze(body, "broken-v1", fail)
        assert index.stats()["analyses"] == 0
        assert cas.path(body).read_bytes() == b"data"
    with pytest.raises(ValueError, match="outside"):
        Index(cas.directory, cas.directory / "index.sqlite3")
    assert not (cas.directory / "index.sqlite3").exists()
