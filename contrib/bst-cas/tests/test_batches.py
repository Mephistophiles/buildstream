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
import pytest

from bst_cas import Index, load_artifacts
from bst_cas.batch import batches


def test_257_owners_need_ten_queries_and_five_tree_validations(cas, database, monkeypatch):
    root = cas.tree(files=[("file", cas.put(b"payload"), False)])
    names = [f"junction-{i}.bst:lib.bst" for i in range(257)]
    calls = []

    class Project:
        def report(self, names, fields):
            calls.append(names)
            return [{"name": name, "full-key": name, "artifact-cas-digest": root} for name in reversed(names)]

    with Index(cas.directory, database) as index:
        reads = []
        original = index.tree
        monkeypatch.setattr(index, "tree", lambda digest: reads.append(digest) or original(digest))
        results = list(load_artifacts(index, Project(), names, {n: n for n in names}))
        assert len(results) == len(names) and all(error is None for _, _, _, error in results)
        assert [name for name, *_ in results] == sorted(names)
        assert len(calls) == 10 and len(reads) == 5
        assert all(len(call) <= 64 for call in calls)
        assert index.stats()["identity_queries"] == 10
        assert index.stats()["shared_tree_reuses"] == 252


def test_batch_failures_are_isolated_and_changed_owner_is_not_published(cas, database):
    root = cas.tree()
    counts = {}

    class Project:
        def report(self, names, fields):
            if "bad.bst" in names:
                raise ValueError("not cached")
            result = []
            for name in names:
                counts[name] = counts.get(name, 0) + 1
                result.append(
                    {
                        "name": name,
                        "full-key": str(counts[name]) if name == "changed.bst" else name,
                        "artifact-cas-digest": root,
                    }
                )
            return result

    with Index(cas.directory, database) as index:
        rows = {
            n: (entries, identity, error)
            for n, entries, identity, error in load_artifacts(index, Project(), ["good.bst", "changed.bst", "bad.bst"])
        }
        assert rows["good.bst"][2] is None
        assert rows["bad.bst"][0] is None and "not cached" in rows["bad.bst"][2]
        assert rows["changed.bst"][0] is None and "identity changed" in rows["changed.bst"][2]


def test_shared_manifest_revalidated_between_batches_after_gc(cas, database):
    body = cas.put(b"payload")
    root = cas.tree(files=[("file", body, False)])

    class Project:
        def report(self, names, fields):
            return [{"name": name, "full-key": name, "artifact-cas-digest": root} for name in names]

    with Index(cas.directory, database) as index:
        results = load_artifacts(index, Project(), [f"{i:03}.bst" for i in range(65)])
        assert next(results)[3] is None
        cas.path(body).unlink()
        rows = list(results)
        assert all(error is None for _, _, _, error in rows[:63])
        assert rows[-1][1] is None and "unavailable" in rows[-1][3]


def test_argument_budget_and_unexpected_identity_records(cas, database):
    names = [str(i) + "x" * 1000 for i in range(100)]
    assert all(sum(len(n.encode()) + 1 for n in batch) <= 24 * 1024 for batch in batches(names))

    class Project:
        def report(self, names, fields):
            return [{"name": "unexpected", "full-key": "key", "artifact-cas-digest": ""}]

    with Index(cas.directory, database) as index:
        rows = list(load_artifacts(index, Project(), ["a", "b"]))
        assert all(entries is None and "Unexpected" in error for _, entries, _, error in rows)
