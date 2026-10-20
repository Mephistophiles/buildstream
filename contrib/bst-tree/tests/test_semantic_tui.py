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
import copy

import pytest
from textual.widgets import DataTable, Input, RichLog, Select

from bst_tree.diff_tui import DiffExplorer
from bst_tree.model import Graph
from bst_tree.semantic_tui import SemanticScreen


def pair():
    nodes = {n: {"kind": "manual", "key": "a", "source_info": None, "workspace": False}
             for n in ("app", "lib[1]", "mid", "other", "tool")}
    old = Graph(["app", "other"], nodes, {
        ("app", "lib[1]"): frozenset(["run"]), ("app", "mid"): frozenset(["run"]),
        ("mid", "lib[1]"): frozenset(["run"]), ("other", "tool"): frozenset(["build"]),
    }, {"strict": True})
    new = copy.deepcopy(old)
    del new.edges["app", "lib[1]"]
    del new.edges["other", "tool"]
    return old, new


@pytest.mark.parametrize("size", [(150, 40), (80, 30)])
async def test_semantic_screen_paths_targets_scopes_and_return(size):
    app = DiffExplorer(*pair())
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        original = app.selected_name
        await pilot.press("m")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SemanticScreen)
        assert screen.category == "retained"
        assert screen.row_names == ["lib[1]"]
        assert screen.selected_name == "lib[1]"
        screen.query_one("#sem-section", Select).value = "paths"
        await pilot.pause()
        assert screen.query_one(RichLog).lines
        screen.query_one("#sem-target", Select).value = "other"
        await pilot.pause()
        assert screen.category == "effects"
        assert screen.row_names == ["tool"]
        screen.query_one("#sem-scope", Select).value = "run"
        await pilot.pause()
        assert screen.row_names == []
        screen.query_one("#sem-scope", Select).value = "build"
        await pilot.pause()
        assert screen.row_names == ["tool"]
        await pilot.press("/", "q")
        await pilot.pause()
        assert screen.query_one(Input).value == "q"
        assert app.is_running
        await pilot.press("escape")
        assert isinstance(screen.focused, DataTable)
        screen.query_one(Input).value = ""
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()
        assert screen.query_one("#sem-section", Select).expanded
        await pilot.press("escape", "escape")
        await pilot.pause()
        assert not isinstance(app.screen, SemanticScreen)
        assert app.selected_name == original


async def test_semantic_start_and_unmatched_targets_are_explicit():
    old, new = pair()
    new.targets = ["lib[1]"]
    app = DiffExplorer(old, new, semantic=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SemanticScreen)
        screen = app.screen
        assert screen.query_one("#sem-target", Select).disabled
        assert screen.row_names == []
        assert screen.query_one(RichLog).lines
        await pilot.press("q")
        await pilot.pause()
        assert not app.is_running


async def test_all_semantic_view_includes_unchanged_only_when_requested():
    app = DiffExplorer(*pair(), semantic=True, show_all=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        await pilot.press("0")
        await pilot.pause()
        assert set(screen.row_names) == {"mid", "lib[1]"}
        assert app.category == "all"  # screen bindings must not modify the underlying inspector


async def test_stale_initial_tab_event_cannot_hide_retained_dependencies():
    from textual.widgets import Tab, Tabs

    app = DiffExplorer(*pair(), semantic=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        tabs = screen.query_one(Tabs)
        assert tabs.active == "sem-retained"
        # The initial Effects event may arrive after mount switched to Retained.
        screen.tab_changed(Tabs.TabActivated(tabs, tabs.query_one("#sem-effects", Tab)))
        assert screen.category == "retained"
        assert screen.row_names == ["lib[1]"]
        assert screen.focused is screen.query_one(DataTable)
