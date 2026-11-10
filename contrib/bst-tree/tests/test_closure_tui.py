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
from dataclasses import replace
import sys

import pytest
from textual.widgets import DataTable, Input, RichLog, Select, Tabs

from bst_tree.closure_tui import ClosureDiffApp
from bst_tree.diff_cli import closure_main
from bst_tree.model import Graph, write_snapshot
from bst_tree.semantic import SemanticDiff


def snapshots():
    names = ("app.bst", "mid.bst", "lib.bst", "bootstrap.bst", "linux-base.bst", "other.bst", "tool.bst")
    nodes = {n: {"kind": "manual", "key": None, "source_info": None, "workspace": False} for n in names}
    old = Graph(
        ["app.bst", "other.bst"],
        nodes,
        {
            ("app.bst", "lib.bst"): frozenset(("build", "run")),
            ("app.bst", "mid.bst"): frozenset(("build",)),
            ("app.bst", "bootstrap.bst"): frozenset(("run",)),
            ("other.bst", "tool.bst"): frozenset(("build",)),
        },
        {},
    )
    new = replace(
        old,
        edges={
            ("app.bst", "lib.bst"): frozenset(("build",)),
            ("app.bst", "mid.bst"): frozenset(("build", "run")),
            ("app.bst", "linux-base.bst"): frozenset(("run",)),
            ("other.bst", "tool.bst"): frozenset(("run",)),
        },
    )
    return old, new


def rows(table):
    return [[cell.plain for cell in table.get_row_at(i)] for i in range(table.row_count)]


def detail(app):
    return "\n".join(line.text for line in app.query_one(RichLog).lines)


@pytest.mark.parametrize("size", [(145, 36), (80, 24)])
async def test_closure_and_edges_keep_roles_and_routes_visible(size):
    app = ClosureDiffApp(SemanticDiff(*snapshots()), scope="run")
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.has_focus
        assert app.query_one(RichLog).size.height >= 4
        assert ["-", "run", "none", "lib.bst"] in rows(table)
        await pilot.press("down")
        assert "build" not in detail(app)
        assert "Before  app.bst → lib.bst" in detail(app)
        assert "After  unreachable" in detail(app)
        await pilot.press("enter")
        assert app.query_one(RichLog).has_focus
        await pilot.press("escape")
        assert table.has_focus

        await pilot.press("e")
        assert app.query_one(Tabs).active == "edges"
        assert not app.query_one("#scope", Select).disabled
        assert rows(table) == [
            ["-", "run", "none", "app.bst → bootstrap.bst"],
            ["-", "run", "none", "app.bst → lib.bst"],
            ["+", "none", "run", "app.bst → linux-base.bst"],
            ["+", "none", "run", "app.bst → mid.bst"],
        ]
        await pilot.press("down")
        assert "Route roles   run → none" in detail(app)
        assert "build" not in detail(app)

        await pilot.press("/", "m", "i", "d")
        assert app.query_one(Input).value == "mid"
        assert rows(table) == [["+", "none", "run", "app.bst → mid.bst"]]
        await pilot.press("escape")
        assert table.has_focus
        app.query_one(Input).value = ""
        app.query_one("#target", Select).value = "other.bst"
        await pilot.pause()
        assert rows(table) == [["+", "none", "run", "other.bst → tool.bst"]]
        await pilot.press("e")
        assert app.query_one("#scope", Select).value == "run"
        assert rows(table) == [["+", "none", "run", "tool.bst"]]
        scope = app.query_one("#scope", Select)
        scope.value = "build"
        await pilot.pause()
        assert rows(table) == [["-", "build", "none", "tool.bst"]]
        scope.value = "all"
        await pilot.pause()
        assert rows(table) == [
            ["-", "build", "none", "tool.bst@build"],
            ["+", "none", "run", "tool.bst@run"],
        ]
        assert "@build  +0 -1 packages" in str(app.query_one("#summary").content)
        assert "@run  +1 -0 packages" in str(app.query_one("#summary").content)


async def test_alternate_runtime_path_preserves_closure_but_not_the_declared_edge():
    old, new = snapshots()
    relay = {
        ("app.bst", "linux-base.bst"): frozenset(("run",)),
        ("linux-base.bst", "lib.bst"): frozenset(("run",)),
    }
    old = replace(old, edges={**old.edges, **relay})
    new = replace(new, edges={**new.edges, **relay})
    app = ClosureDiffApp(SemanticDiff(old, new, ["app.bst"]), scope="run")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(Input).value = "lib.bst"
        await pilot.pause()
        assert rows(app.query_one(DataTable)) == [["=", "run", "run", "lib.bst"]]
        assert "Roles in scope   run" in detail(app)
        assert "run → run" not in detail(app)
        assert "After  app.bst → linux-base.bst → lib.bst" in detail(app)
        await pilot.press("e")
        assert rows(app.query_one(DataTable)) == [["-", "run", "none", "app.bst → lib.bst"]]
        app.query_one("#scope", Select).value = "all"
        await pilot.pause()
        assert rows(app.query_one(DataTable)) == [["~", "build/run", "build", "app.bst → lib.bst"]]


async def test_declared_edge_shows_nested_consumer_package_delta():
    old, new = snapshots()
    parent = ("other.bst", "app.bst")
    old = replace(old, edges={**old.edges, parent: frozenset(("build",))})
    new = replace(new, edges={**new.edges, parent: frozenset(("build",))})
    app = ClosureDiffApp(SemanticDiff(old, new, ["other.bst"]))
    async with app.run_test(size=(145, 36)) as pilot:
        await pilot.pause()
        await pilot.press("e")
        app.query_one(Input).value = "app.bst → lib.bst"
        await pilot.pause()
        assert "app.bst@run  +2 -2 packages" in detail(app)
        assert "- lib.bst@run" in detail(app)


async def test_target_coverage_is_explicit():
    old, new = snapshots()
    model = SemanticDiff(replace(old, targets=["app.bst"]), replace(new, targets=["other.bst"]))
    app = ClosureDiffApp(model)
    async with app.run_test() as pilot:
        await pilot.pause()
        coverage = str(app.query_one("#coverage").content)
        assert "Only before: app.bst" in coverage
        assert "Only after: other.bst" in coverage
        assert app.query_one("#target", Select).disabled
        assert app.query_one(DataTable).row_count == 0
        await pilot.press("e", "q")
        assert not app.is_running


def test_standalone_cli_opens_the_new_app(tmp_path, monkeypatch):
    old, new = snapshots()
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    write_snapshot(old, before)
    write_snapshot(new, after)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    launched = []
    monkeypatch.setattr(ClosureDiffApp, "run", lambda app: launched.append(app))
    assert closure_main([str(before), str(after), "--tui", "--scope", "run", "--check"]) == 1
    assert len(launched) == 1
    assert launched[0].initial_scope == "run"
    assert launched[0].labels == ("before.json", "after.json")
