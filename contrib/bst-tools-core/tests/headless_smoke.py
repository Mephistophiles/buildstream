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
import subprocess
import sys
import tarfile
import tempfile

assert find_spec("textual") is None, "Headless job must not install the TUI extra"

from bst_diff.cli import main as artifact_diff
from bst_tree.cli import main as tree
from bst_tree.model import Graph, write_snapshot

for removed in ("bst_tree.artifacts", "bst_tree.artifact_cli", "bst_tree.artifact_tui", "bst_tree.artifact_process"):
    assert find_spec(removed) is None, f"Removed module leaked into the wheel: {removed}"


def run_tool(name, *args, input=None):
    executable = Path(sys.executable).parent / name
    return subprocess.run([executable, *args], input=input, text=True, capture_output=True)


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
    app, library = "app.bst", "lib.bst"
    graph.nodes[library] = dict(graph.nodes[app])
    graph.edges[app, library] = frozenset({"run"})
    write_snapshot(graph, snapshot)
    graph.edges[app, library] = frozenset({"build"})
    updated = root / "updated.json"
    write_snapshot(graph, updated)

    graph_delta = run_tool(
        "bst-graph-diff", str(snapshot), "-", "--format", "json", "--check", input=updated.read_text()
    )
    assert graph_delta.returncode == 1, graph_delta.stderr
    assert json.loads(graph_delta.stdout)["edges"][0]["new"] == ["build"]

    closure_delta = run_tool(
        "bst-closure-diff", str(snapshot), str(updated), "--scope", "run", "--format", "json", "--check"
    )
    assert closure_delta.returncode == 1, closure_delta.stderr
    assert json.loads(closure_delta.stdout)["targets"][0]["changes"] == [
        {"name": library, "change": "removed", "old": ["run"], "new": []}
    ]

    explanation = run_tool("bst-key-explain", str(snapshot), str(updated), "--format", "json")
    assert explanation.returncode == 0, explanation.stderr
    assert json.loads(explanation.stdout)["records"][0]["evidence"] == ["dependencies"]

    chains = run_tool("bst-chains", app, library, "--snapshot", "-", input=updated.read_text())
    assert chains.returncode == 0, chains.stderr
    assert chains.stdout == f"{app} -> {library}\n"

    rebuild = run_tool("bst-rebuild-select", app, library, "--snapshot", str(updated))
    assert rebuild.returncode == 0, rebuild.stderr
    assert rebuild.stdout == f"{library} {app}\n"

    size = run_tool("bst-du", "--archive", app, archives[0], "--format", "json")
    assert size.returncode == 0, size.stderr
    assert json.loads(size.stdout)["total"]["file_bytes"] == len(b"before\n")
assert not any(name == "textual" or name.startswith("textual.") for name in sys.modules)
