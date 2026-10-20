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

from bst_blame.index import batches, collect, normalize_path, parse_listings
from bst_blame.project import Graph
from helpers import listing, node


def test_paired_lists_preserve_spaces_arrows_and_links():
    names = ["sdk.bst:dir/code.bst"]
    entries = {
        names[0]: [
            ("usr", "dir", 0, None),
            ("usr/  file -> name", "reg", 123, None),
            ("usr/link -> name", "link", 8, "../target -> name"),
            ("usr/run", "exe", 4, None),
        ]
    }
    result = parse_listings(listing(names, entries), listing(names, entries, long=True), names)
    rows = result[names[0]]
    assert rows[1]["path"] == "/usr/  file -> name"
    assert rows[2]["path"] == "/usr/link -> name"
    assert rows[2]["link_target"] == "../target -> name"
    assert rows[3]["executable"] is True


def test_empty_artifact_and_real_file_named_like_empty_message():
    names = ["empty.bst", "real.bst"]
    entries = {"real.bst": [("This element has no associated artifacts", "reg", 0, None)]}
    result = parse_listings(listing(names, entries), listing(names, entries, long=True), names)
    assert result["empty.bst"] == []
    assert result["real.bst"][0]["kind"] == "file"


@pytest.mark.parametrize("path", ["../bad", "/usr/../bad", "a\nb", "a\x00b", ""])
def test_bad_paths(path):
    with pytest.raises(ValueError):
        normalize_path(path)


def test_path_normalization_is_artifact_relative_and_not_host_relative():
    assert normalize_path("./usr//lib/a") == "/usr/lib/a"
    assert normalize_path("/usr/lib/a") == "/usr/lib/a"
    assert normalize_path("/**") == "/**"


def test_batches_keep_junction_collisions_separate_and_bound_arguments():
    names = ["a.bst:lib.bst", "b.bst:lib.bst", "other.bst", "z.bst"]
    result = list(batches(names, limit=2))
    assert [name for batch in result for name in batch] == sorted(names)
    assert all(len(batch) <= 2 for batch in result)
    assert all(len({name.rsplit(":", 1)[-1] for name in batch}) == len(batch) for batch in result)


@pytest.mark.parametrize("change", ["mismatch", "unknown", "duplicate", "newline"])
def test_rejects_ambiguous_or_changing_output(change):
    entries = {"app.bst": [("a", "reg", 1, None)]}
    plain, detail = listing(["app.bst"], entries), listing(["app.bst"], entries, long=True)
    if change == "mismatch":
        detail = detail.replace(" a\n", " b\n")
    elif change == "unknown":
        plain = plain.replace("app.bst:", "other.bst:")
    elif change == "duplicate":
        plain += plain
    else:
        plain = plain.replace("\ta\n", "\ta\nb\n")
    with pytest.raises(ValueError):
        parse_listings(plain, detail, ["app.bst"])


def test_local_coverage_shared_paths_and_batch_failure_isolation():
    nodes = {
        "root.bst": node(kind="stack", run=["a.bst", "b.bst", "missing.bst"]),
        "a.bst": node(),
        "b.bst": node("failed"),
        "missing.bst": node("buildable"),
    }
    graph = Graph(nodes, ["root.bst"], set(nodes), "all")
    calls = []

    class Project:
        def listings(self, names):
            calls.append(names)
            if "b.bst" in names:
                raise ValueError("cached artifact disappeared")
            entries = {"a.bst": [("usr/lib/a", "reg", 4, None)]}
            return listing(names, entries), listing(names, entries, long=True)

    index, coverage = collect(Project(), graph, "/usr/lib/*", True)
    assert index["/usr/lib/a"][0]["dependency_paths"] == [["root.bst", "a.bst"]]
    assert coverage["inspected"] == 1
    assert coverage["complete"] is False
    assert coverage["missing"] == [{"element": "missing.bst", "state": "buildable"}]
    assert coverage["errors"][0]["element"] == "b.bst"
    assert coverage["skipped"] == ["root.bst"]
    assert not any("missing.bst" in names for names in calls)


def test_same_named_junction_elements_have_distinct_owners():
    names = ["a.bst:lib.bst", "b.bst:lib.bst"]
    nodes = {name: node() for name in names}
    graph = Graph(nodes, names, set(names), "all")

    class Project:
        def listings(self, names):
            entries = {name: [("usr/lib/a", "reg", 4, None)] for name in names}
            return listing(names, entries), listing(names, entries, long=True)

    index, coverage = collect(Project(), graph, "/usr/lib/a")
    assert [o["element"] for o in index["/usr/lib/a"]] == names
    assert coverage["complete"] is True
