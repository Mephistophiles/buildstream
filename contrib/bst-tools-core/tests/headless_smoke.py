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
"""Run in a fresh environment with only the two base CLI packages installed."""

from importlib.util import find_spec
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile

assert find_spec("textual") is None, "Headless job must not install the TUI extra"

from bst_diff.cli import main as artifact_diff
from bst_tree.cli import main as tree
from bst_tree.model import Graph, write_snapshot

for removed in ("bst_tree.artifacts", "bst_tree.artifact_cli", "bst_tree.artifact_tui", "bst_tree.artifact_process"):
    assert find_spec(removed) is None, f"Removed module leaked into the wheel: {removed}"

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    archives = []
    for name in ("before", "after"):
        path = root / (name + ".tar")
        data = (name + "\n").encode()
        with tarfile.open(path, "w") as archive:
            member = tarfile.TarInfo("config")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        archives.append(str(path))
    assert artifact_diff([*archives, "--archives", "--format", "json", "--check"]) == 1
    assert artifact_diff([archives[0], archives[0], "--archives", "--check"]) == 0
    graph = Graph(
        ["app.bst"], {"app.bst": {"kind": "import", "key": None, "source_info": None, "workspace": False}}, {}, {}
    )
    snapshot = root / "graph.json"
    write_snapshot(graph, snapshot)
    assert tree(["tree", "--snapshot", str(snapshot), "--format", "json"]) == 0
    assert tree(["diff", str(snapshot), str(snapshot), "--check"]) == 0
    assert tree(["size", "--archive", "app.bst", archives[0], "--format", "json"]) == 0
assert not any(name == "textual" or name.startswith("textual.") for name in sys.modules)
