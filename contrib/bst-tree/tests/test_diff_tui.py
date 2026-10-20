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
from textual.widgets import DataTable, Input, RichLog, Select, Static, Tabs

from bst_tree.diff_tui import DiffExplorer
from bst_tree.model import DETAIL_FIELDS, Graph


def pair():
    nodes = {
        n: {"kind": "manual", "key": "a" * 64, "source_info": None, "workspace": False}
        for n in ("app.bst", "tool[1].bst", "mystery.bst")
    }
    old = Graph(
        ["app.bst"],
        nodes,
        {("app.bst", "tool[1].bst"): frozenset(["build"])},
        {"strict": True},
        {n: {f: {} for f in DETAIL_FIELDS} for n in nodes},
    )
    new = copy.deepcopy(old)
    new.details["tool[1].bst"]["env"] = {"CFLAGS": "-O3"}
    for node in new.nodes.values():
        node["key"] = "b" * 64
    return old, new


@pytest.mark.parametrize("size", [(150, 40), (80, 25)])
async def test_diff_navigation_filters_and_sections(size):
    old, new = pair()
    app = DiffExplorer(old, new)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert app.category == "local"
        assert app.row_names == ["tool[1].bst"]
        assert app.selected_name == "tool[1].bst"
        assert "tool[1].bst" in str(app.query_one("#selection", Static).render())
        select = app.query_one(Select)
        select.value = "env"
        await pilot.pause()
        assert app.query_one(RichLog).lines
        await pilot.press("c")
        assert app.full_context
        await pilot.press("2")
        await pilot.pause()
        assert app.row_names == ["app.bst"]
        assert app.selected_name == "app.bst"
        select.value = "candidates"
        await pilot.pause()
        await pilot.press("3")
        await pilot.pause()
        assert app.row_names == ["mystery.bst"]
        await pilot.press("0", "/")
        await pilot.press(*"missing")
        await pilot.pause()
        assert app.row_names == []
        assert app.selected_name is None
        await pilot.press("escape")
        assert isinstance(app.focused, DataTable)
        app.query_one(Input).value = ""
        await pilot.pause()
        assert len(app.row_names) == 3
        await pilot.press("f")
        await pilot.pause()
        assert app.query_one(Select).expanded
        await pilot.press("escape", "/", "q")
        await pilot.pause()
        assert app.query_one(Input).value == "q"
        assert app.is_running


async def test_no_changes_legacy_and_metadata_only():
    old, new = pair()
    old.details = new.details = None
    new.nodes = copy.deepcopy(old.nodes)
    new.metadata = {"strict": True, "bst_version": "new"}
    app = DiffExplorer(old, new)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.row_names == []
        assert "unavailable" in str(app.query_one("#warning", Static).render())
        assert app.query_one(RichLog).lines


async def test_all_and_filtered_comparison():
    old, new = pair()
    app = DiffExplorer(old, new, fields=["details"], show_all=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.row_names) == 3
        app.query_one(DataTable).move_cursor(row=2)
        await pilot.pause()
        assert app.query_one(RichLog).lines
        await pilot.press("1")
        await pilot.pause()
        assert app.row_names == ["tool[1].bst"]


async def test_sources_view_and_history_toggle():
    from test_input_diff import pair as provenance_pair

    old, new = provenance_pair()
    app = DiffExplorer(old, new)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.selected_name == "app.bst"
        app.query_one(Select).value = "sources"
        await pilot.pause()
        assert app.query_one(RichLog).lines
        await pilot.press("h")
        await pilot.pause()
        assert app.show_history and app.query_one(Select).value == "sources"
        await pilot.press("h")
        await pilot.pause()
        assert not app.show_history
        # Ordinary section diffs also include the corresponding input sources.
        app.query_one(Select).value = "env"
        await pilot.pause()
        assert app.query_one(RichLog).lines
