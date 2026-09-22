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
from textual.widgets import Input, Static, Tree

from bst_tree.model import Graph
from bst_tree.tui import Explorer
from test_core import graph, node


async def test_navigation_search_reverse_and_scope():
    app = Explorer(graph=graph())
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        root = tree.root.children[0]
        tree.select_node(root)
        await pilot.press("space")
        await pilot.pause()
        assert len(root.children) == 2
        await pilot.press("/")
        app.query_one(Input).value = "bootstrap"
        await pilot.press("enter")
        await pilot.pause()
        assert app.selected() == "bootstrap.bst"
        await pilot.press("w")
        assert "compiler.bst" in str(app.query_one("#details", Static).render())
        await pilot.press("r")
        await pilot.pause()
        assert app.reverse_root == "bootstrap.bst"
        await pilot.press("escape")
        await pilot.pause()
        assert app.selected() == "bootstrap.bst"
        await pilot.press("s")
        assert app.scope == "run"
        assert "compiler.bst" not in app.view.nodes
        await pilot.resize_terminal(50, 15)
        await pilot.pause()


async def test_large_graph_stays_lazy():
    nodes = {f"n{i}": node() for i in range(10000)}
    edges = {(f"n{i}", f"n{i+1}"): frozenset(["run"]) for i in range(9999)}
    app = Explorer(graph=Graph(["n0"], nodes, edges, {}))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(Tree)
        assert len(tree.root.children) == 1
        assert len(tree.root.children[0].children) == 0


async def test_loader_error():
    def fail():
        raise ValueError("broken project")

    app = Explorer(loader=fail)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.load_error == "broken project"


async def test_reverse_children_and_cycle_guard():
    g = graph()
    g.edges["sub:lib.bst", "app.bst"] = frozenset(["run"])
    app = Explorer(graph=g)
    async with app.run_test() as pilot:
        await app.reveal(["app.bst", "sub:lib.bst"])
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        tree = app.query_one(Tree)
        root = tree.root.children[0]
        root.expand()
        await pilot.pause()
        assert {child.data[-1] for child in root.children} == {"app.bst", "compiler.bst"}


async def test_quit_cancels_loader():
    import threading

    cancelled = threading.Event()

    def load():
        cancelled.wait(timeout=5)
        return graph()

    app = Explorer(loader=load, cancel_loader=cancelled.set)
    async with app.run_test() as pilot:
        await pilot.press("q")
    assert cancelled.is_set()


async def test_arrow_keys_and_literal_labels():
    app = Explorer(graph=graph())
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("right")
        await pilot.pause()
        root = tree.root.children[0]
        assert root.is_expanded
        assert "[build]" in str(root.children[0].label)
        await pilot.press("left")
        assert not root.is_expanded
