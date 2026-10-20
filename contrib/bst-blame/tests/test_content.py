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
import hashlib
import io
import tarfile
import base64
import sys

import pytest

from bst_blame import content


def owner(name="a.bst", *, kind="file", size=4, executable=False, target=None):
    return {
        "element": name,
        "kind": kind,
        "size": size,
        "executable": executable,
        "link_target": target,
        "artifact_key": "a" * 64,
        "state": "cached",
        "dependency_paths": [[name]],
    }


def archive(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as bundle:
        for path, data, kind in entries:
            member = tarfile.TarInfo(path)
            if kind == "file":
                member.size = len(data)
                member.mode = 0o644
                bundle.addfile(member, io.BytesIO(data))
            else:
                member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                member.linkname = data
                bundle.addfile(member)
    stream.seek(0)
    return stream


def test_hashes_regular_files_without_extracting_archive():
    stream = archive([("./usr/lib/a", b"test", "file"), ("./link", "/etc/passwd", "symlink")])
    result = content.inspect_archive(stream, {"/usr/lib/a": owner()}, text=True)
    assert result["/usr/lib/a"] == {"sha256": hashlib.sha256(b"test").hexdigest(), "_text": "test"}


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_never_follows_links_even_when_listing_claimed_regular_file(kind):
    with pytest.raises(ValueError, match="not followed"):
        content.inspect_archive(archive([("./a", "/etc/passwd", kind)]), {"/a": owner()})


@pytest.mark.parametrize("name", ["/absolute", "../outside"])
def test_rejects_malformed_tar_paths(name):
    with pytest.raises(ValueError):
        content.inspect_archive(archive([(name, b"test", "file")]), {"/a": owner()})


def test_missing_and_duplicate_members_are_incomplete():
    with pytest.raises(ValueError, match="disappeared"):
        content.inspect_archive(archive([]), {"/a": owner()})
    with pytest.raises(ValueError, match="Duplicate"):
        content.inspect_archive(archive([("a", b"test", "file"), ("a", b"test", "file")]), {"/a": owner()})


def test_binary_and_large_files_are_hashed_without_retaining_text():
    for data in (b"a\x00bc", b"x" * (content.TEXT_LIMIT + 1), b"\xffabc"):
        result = content.inspect_archive(archive([("a", data, "file")]), {"/a": owner(size=len(data))}, text=True)
        assert "_text" not in result["/a"]
        assert result["/a"]["sha256"] == hashlib.sha256(data).hexdigest()


def test_same_size_is_not_evidence_of_same_contents():
    owners = [owner(), owner("b.bst")]
    record = content.describe("/a", owners)
    assert record["relationship"] == "potential-overlap"
    assert record["content_comparison"] == "not-compared"
    assert record["metadata_equal"] is True


def test_different_content_and_bounded_diff():
    owners = [owner(), owner("b.bst")]
    for item, data in zip(owners, ("old\n" * 150, "new\n" * 150)):
        item.update(sha256=hashlib.sha256(data.encode()).hexdigest(), _text=data)
    record = content.describe("/a", owners, compared=True, text=True)
    assert record["content_comparison"] == "different"
    assert len(record["diffs"][0]["lines"]) == content.DIFF_LIMIT
    assert record["diffs"][0]["truncated"] is True
    assert "_text" not in record["owners"][0]


def test_identical_content_metadata_difference_and_missing_hash():
    a, b = owner(), owner("b.bst", executable=True)
    a["sha256"] = b["sha256"] = "a" * 64
    record = content.describe("/a", [a, b], compared=True)
    assert record["content_comparison"] == "identical"
    assert record["metadata_equal"] is False
    del b["sha256"]
    assert content.describe("/a", [a, b], compared=True)["content_comparison"] == "incomplete"


def test_symlink_targets_compared_and_shared_directories_not_overlaps():
    a, b = owner(kind="symlink", target="a"), owner("b.bst", kind="symlink", target="b")
    assert content.describe("/link", [a, b])["content_comparison"] == "different"
    a, b = owner(kind="directory"), owner("b.bst", kind="directory")
    assert content.describe("/dir", [a, b])["relationship"] == "shared-directory"


def test_comparison_failure_preserves_owners_and_marks_coverage(monkeypatch):
    index = {"/a": [owner(), owner("b.bst")]}
    coverage = {"complete": True, "errors": []}

    def failure(*args, **kwargs):
        raise ValueError("checkout failed")

    monkeypatch.setattr(content, "fingerprints", failure)
    content.compare(object(), index, coverage)
    assert coverage["complete"] is False
    assert len(coverage["errors"]) == 2
    assert len(index["/a"]) == 2


def test_streamed_export_drains_large_tar_padding_and_checks_key():
    stream = archive([("a", b"test", "file")])
    encoded = base64.b64encode(stream.getvalue()).decode("ascii")

    class Project:
        key = "a" * 64

        def checkout_command(self, name):
            # Generate padding in the child to stay below per-argument limits.
            return [
                sys.executable,
                "-c",
                "import sys,base64; sys.stdout.buffer.write(base64.b64decode("
                + repr(encoded)
                + ") + b'\\0' * 200000)",
            ]

        def report(self, names, fields):
            assert names == ["a.bst"]
            assert fields == ("full-key",)
            return [{"full-key": self.key}]

    project = Project()
    result = content.fingerprints(project, "a.bst", {"/a": owner()}, text=True)
    assert result["/a"]["_text"] == "test"
    project.key = "b" * 64
    with pytest.raises(ValueError, match="key changed"):
        content.fingerprints(project, "a.bst", {"/a": owner()})


def test_newline_only_difference_is_visible():
    owners = [owner(), owner("b.bst")]
    for item, data in zip(owners, ("same", "same\n")):
        item.update(sha256=hashlib.sha256(data.encode()).hexdigest(), _text=data)
    record = content.describe("/a", owners, compared=True, text=True)
    assert "\\ No newline at end of file\n" in record["diffs"][0]["lines"]
