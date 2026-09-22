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


@pytest.mark.skipif(
    os.environ.get("BST_TREE_TEST_LIVE") != "1", reason="Set BST_TREE_TEST_LIVE=1 with bst/buildbox installed"
)
def test_live_buildstream(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    elements = project / "elements"
    elements.mkdir(parents=True)
    (project / "project.conf").write_text("name: tree-test\nmin-version: '2.0'\nelement-path: elements\n")
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
