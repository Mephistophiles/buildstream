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
from rich.style import Style
from textual.events import Key
from textual.widgets import Button, Input, Static, TextArea, Tree

from bst_tree.model import Graph
from bst_tree.tui import Explorer
from bst_tree.inspection import ArtifactEntry, Inspection
from bst_tree.inspection_tui import ArtifactTree, ElementMenu, InspectionScreen, WorkspaceTree
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
        await pilot.press("S")
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
        assert root.is_expanded
        assert tree.cursor_node is root
        assert {child.data[-1] for child in root.children} == {"app.bst", "compiler.bst"}
        assert all(not child.is_expanded for child in root.children)
        await pilot.press("down", "right")
        await pilot.pause()
        cycle = root.children[0].children[0]
        assert cycle.data[-1] == "sub:lib.bst"
        assert not cycle.allow_expand
        await pilot.press("escape")
        await pilot.pause()
        assert app.selected() == "sub:lib.bst"


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


async def test_search_escape_preserves_reverse_and_root_clears_details():
    app = Explorer(graph=graph())
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.pause()
        await pilot.press("r", "/", "a", "b", "c", "q", "s")
        assert app.query_one(Input).value == "abcqs"
        await pilot.press("escape")
        assert app.reverse_root == "app.bst"
        await pilot.press("escape")
        await pilot.pause()
        tree.move_cursor(tree.root)
        await pilot.pause()
        assert "Select an element" in str(app.query_one("#details", Static).render())


async def test_queued_expand_and_scope_change():
    app = Explorer(graph=graph())
    async with app.run_test() as pilot:
        await app.reveal(["app.bst", "compiler.bst"])
        await pilot.pause()
        # Queue both keys before the expansion event reaches the application.
        app.post_message(Key("right", None))
        app.post_message(Key("S", "S"))
        await pilot.pause()
        assert app.scope == "run"
        assert "compiler.bst" not in app.view.nodes
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("right")
        assert [child.data[-1] for child in tree.root.children[0].children] == ["sub:lib.bst"]


async def test_stale_tree_events_after_rebuild():
    app = Explorer(graph=graph())
    async with app.run_test() as pilot:
        old_node = await app.reveal(["app.bst", "compiler.bst"])
        await pilot.pause()
        app.rebuild()
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.pause()
        before = str(app.query_one("#details", Static).render())
        # The name still exists and node IDs may be reused, but this occurrence
        # belongs to the previous tree and must not populate or change details.
        app.post_message(Tree.NodeExpanded(old_node))
        app.post_message(Tree.NodeHighlighted(old_node))
        await pilot.pause()
        assert not old_node.children
        assert str(app.query_one("#details", Static).render()) == before


class FakeInspector:
    def __init__(self, workspace=None, error=None):
        self.calls = []
        self.cancelled = False
        self.workspace = workspace
        self.error = error

    def load(self, name, section):
        self.calls.append((name, section))
        if self.error:
            raise ValueError(self.error)
        return Inspection("literal [build] contents", self.workspace)

    def cancel(self):
        self.cancelled = True


async def test_element_menu_and_inspection_preserve_graph():
    inspector = FakeInspector()
    app = Explorer(graph=graph(), inspector_factory=lambda: inspector)
    async with app.run_test(size=(50, 15)) as pilot:
        tree = app.query_one(Tree)
        root = tree.root.children[0]
        tree.move_cursor(root)
        await pilot.press("right", "m")
        assert isinstance(app.screen, ElementMenu)
        assert await pilot.click("#build")
        await pilot.pause()
        assert isinstance(app.screen, InspectionScreen)
        assert app.screen.query_one(TextArea).text == "literal [build] contents"
        assert inspector.calls == [("app.bst", "build")]
        await pilot.press("s", "r", "j")
        assert app.scope == "all"
        assert app.reverse_root is None
        await pilot.press("escape")
        assert inspector.cancelled
        assert tree.cursor_node is root
        assert root.is_expanded
        await pilot.press("a")
        await pilot.pause()
        assert inspector.calls[-1] == ("app.bst", "artifacts")


async def test_snapshot_actions_explain_unavailability():
    app = Explorer(graph=graph())
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("a")
        assert "Live project required" in str(app.query_one("#status", Static).render())
        await pilot.press("m")
        assert app.screen.query_one("#artifacts", Button).disabled
        await pilot.press("escape")
        assert not isinstance(app.screen, ElementMenu)


async def test_menu_keyboard_focus_and_activation():
    inspector = FakeInspector()
    app = Explorer(graph=graph(), inspector_factory=lambda: inspector)
    async with app.run_test(size=(50, 15)) as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("m")
        app.screen.query_one("#artifacts", Button).focus()
        await pilot.press("tab")
        assert app.focused is app.screen.query_one("#build", Button)
        await pilot.press("shift+tab")
        assert app.focused is app.screen.query_one("#artifacts", Button)
        await pilot.press("tab", "enter")
        await pilot.pause()
        assert isinstance(app.screen, InspectionScreen)
        assert inspector.calls == [("app.bst", "build")]


async def test_inspection_error_keeps_tree_open():
    app = Explorer(graph=graph(), inspector_factory=lambda: FakeInspector(error="Artifact not cached"))
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("a")
        await pilot.pause()
        assert "Artifact not cached" in app.screen.query_one(TextArea).text
        await pilot.press("escape")
        assert app.selected() == "app.bst"


async def test_workspace_file_browser(tmp_path):
    source = tmp_path / "main.c"
    content = "int main() {}\n" * 100
    source.write_text(content)
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    app = Explorer(graph=graph(), inspector_factory=lambda: FakeInspector(workspace=tmp_path))
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("s")
        await pilot.pause()
        workspace = app.screen.query_one(WorkspaceTree)
        await pilot.pause()
        assert len(workspace.root.children) == 1
        workspace.select_node(workspace.root.children[0])
        await pilot.pause()
        preview = app.screen.query_one(TextArea)
        assert preview.text == content
        await pilot.press("tab")
        assert app.focused is preview
        await pilot.press("pagedown")
        assert preview.cursor_location[0] > 0
        await pilot.press("shift+tab")
        assert app.focused is workspace
        await pilot.press("escape")
        assert app.selected() == "app.bst"


async def test_closing_inspection_cancels_pending_load():
    import threading

    started, cancelled = threading.Event(), threading.Event()

    class SlowInspector:
        def load(self, name, section):
            started.set()
            cancelled.wait(timeout=5)
            return Inspection("late result")

        def cancel(self):
            cancelled.set()

    app = Explorer(graph=graph(), inspector_factory=SlowInspector)
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("a")
        await pilot.pause()
        assert started.is_set()
        assert app.screen.query_one(TextArea).text == "Loading…"
        await pilot.press("escape")
        await pilot.pause()
        assert cancelled.is_set()
        assert app.selected() == "app.bst"
        assert "late result" not in str(app.query_one("#details", Static).render())


async def test_menu_visible_shortcuts_and_arrow_navigation():
    inspector = FakeInspector()
    app = Explorer(graph=graph(), inspector_factory=lambda: inspector)
    async with app.run_test(size=(50, 15)) as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("m")
        for key, button in [("a", "artifacts"), ("b", "build"), ("s", "sources")]:
            assert f" {key} " in app.screen.query_one(f"#{button}", Button).label.plain
        app.screen.query_one("#artifacts", Button).focus()
        await pilot.press("down", "enter")
        await pilot.pause()
        assert inspector.calls == [("app.bst", "build")]
        await pilot.press("escape", "m", "s")
        await pilot.pause()
        assert inspector.calls[-1] == ("app.bst", "sources")
        assert app.scope == "all"


@pytest.mark.parametrize("keys", [("down", "up", "left", "right"), ("j", "k", "h", "l")])
async def test_artifact_file_navigation_and_info(keys):
    down, up, left, right = keys

    class ArtifactInspector(FakeInspector):
        def load(self, name, section):
            return Inspection("Artifact overview", artifacts=[
                ArtifactEntry("alpha", "-rw-r--r-- reg 0 alpha"),
                ArtifactEntry("zdir", "drwxr-xr-x dir 0 zdir", True),
                ArtifactEntry("usr/z", "-rwxr-xr-x exe 70 usr/z"),
                ArtifactEntry("usr", "drwxr-xr-x dir 0 usr", True),
                ArtifactEntry("usr/a [b]", "-rw-r--r-- reg 42 usr/a [b]"),
            ])

    app = Explorer(graph=graph(), inspector_factory=ArtifactInspector)
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("a")
        await pilot.pause()
        files = app.screen.query_one(ArtifactTree)
        assert app.focused is files
        assert [str(node.label) for node in files.root.children] == ["usr", "zdir", "alpha"]
        files.move_cursor(files.root)
        await pilot.press(down, right, down)
        assert files.cursor_node.data.path == "usr/a [b]"
        label = files.render_label(files.cursor_node, Style(), Style())
        assert label.plain == "📄 a [b]"
        assert "42" in app.screen.query_one(TextArea).text
        await pilot.press(down)
        assert files.cursor_node.data.path == "usr/z"
        assert "70" in app.screen.query_one(TextArea).text
        await pilot.press(up)
        assert files.cursor_node.data.path == "usr/a [b]"
        await pilot.press(left)
        assert files.cursor_node.data.path == "usr"
        label = files.render_label(files.cursor_node, Style(), Style())
        assert label.plain == "📂 usr"
        await pilot.press(left)
        assert not files.cursor_node.is_expanded
        await pilot.press("i")
        assert app.screen.query_one(TextArea).text == "Artifact overview"
        await pilot.press("tab")
        assert app.focused is app.screen.query_one(TextArea)
        await pilot.press("escape")
        assert app.selected() == "app.bst"


async def test_source_vim_navigation_and_shortcut(tmp_path):
    directory = tmp_path / "src"
    directory.mkdir()
    (directory / "a.c").write_text("source preview")
    (directory / "b.c").write_text("other source")
    app = Explorer(graph=graph(), inspector_factory=lambda: FakeInspector(workspace=tmp_path))
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("s")
        await pilot.pause()
        files = app.screen.query_one(WorkspaceTree)
        await pilot.pause()
        files.move_cursor(files.root)
        await pilot.press("j", "l")
        await pilot.pause()
        await pilot.press("l")
        assert files.cursor_node.data.path == directory / "a.c"
        await pilot.press("j")
        assert files.cursor_node.data.path == directory / "b.c"
        await pilot.press("k", "enter")
        await pilot.pause()
        assert app.screen.query_one(TextArea).text == "source preview"
        await pilot.press("h")
        assert files.cursor_node.data.path == directory
        await pilot.press("h")
        assert not files.cursor_node.is_expanded
        assert app.scope == "all"
        await pilot.press("escape", "S")
        assert app.scope == "run"


async def test_source_checkout_action_and_information(tmp_path):
    (tmp_path / "main.c").write_text("source text")

    class SourceInspector(FakeInspector):
        def load(self, name, section):
            return Inspection("Source provenance", can_checkout=True)

        def checkout_sources(self, name):
            self.calls.append((name, "checkout"))
            return Inspection("Temporary checkout", tmp_path)

    inspector = SourceInspector()
    app = Explorer(graph=graph(), inspector_factory=lambda: inspector)
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.query_one("#load-sources", Button).display
        await pilot.press("f")
        await pilot.pause()
        files = app.screen.query_one(WorkspaceTree)
        await pilot.pause()
        assert inspector.calls == [("app.bst", "checkout")]
        assert not app.screen.query_one("#load-sources", Button).display
        files.select_node(files.root.children[0])
        await pilot.pause()
        assert app.screen.query_one(TextArea).text == "source text"
        await pilot.press("i")
        assert "Source provenance" in app.screen.query_one(TextArea).text
        assert "Temporary checkout" in app.screen.query_one(TextArea).text
        await pilot.press("f")
        assert len(inspector.calls) == 1
        await pilot.press("escape")
        assert inspector.cancelled


async def test_source_checkout_error_can_retry(tmp_path):
    class SourceInspector(FakeInspector):
        def load(self, name, section):
            return Inspection("Source provenance", can_checkout=True)

        def checkout_sources(self, name):
            self.calls.append(name)
            if len(self.calls) == 1:
                raise ValueError("Source remote unavailable")
            return Inspection("Temporary checkout", tmp_path)

    inspector = SourceInspector()
    app = Explorer(graph=graph(), inspector_factory=lambda: inspector)
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("s")
        await pilot.pause()
        assert await pilot.click("#load-sources")
        await pilot.pause()
        assert "Source remote unavailable" in app.screen.query_one(TextArea).text
        assert not app.screen.query_one("#load-sources", Button).disabled
        await pilot.press("f")
        await pilot.pause()
        assert app.screen.query_one(WorkspaceTree)
        assert len(inspector.calls) == 2


async def test_close_during_source_checkout():
    import threading

    started, cancelled, finished = threading.Event(), threading.Event(), threading.Event()

    class SourceInspector(FakeInspector):
        def load(self, name, section):
            return Inspection("Source provenance", can_checkout=True)

        def checkout_sources(self, name):
            started.set()
            cancelled.wait(timeout=5)
            finished.set()
            raise ValueError("Checkout cancelled")

        def cancel(self):
            cancelled.set()

    app = Explorer(graph=graph(), inspector_factory=SourceInspector)
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()
        assert started.is_set()
        await pilot.press("escape")
        await pilot.pause()
        assert cancelled.is_set()
        assert finished.is_set()
        assert app.selected() == "app.bst"


async def test_artifact_preview_and_retry():
    class ArtifactInspector(FakeInspector):
        def load(self, name, section):
            return Inspection("Artifact overview", artifacts=[
                ArtifactEntry("file.txt", "-rw-r--r-- reg 7 file.txt"),
                ArtifactEntry("link", "lrwxrwxrwx link 0 link -> file.txt"),
            ])

        def preview_artifact(self, name, path):
            self.calls.append((name, path))
            if len(self.calls) == 1:
                raise ValueError("Export failed")
            return "Preview [text]\n" * 100

    inspector = ArtifactInspector()
    app = Explorer(graph=graph(), inspector_factory=lambda: inspector)
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("a")
        await pilot.pause()
        files = app.screen.query_one(ArtifactTree)
        files.move_cursor(files.root.children[0])
        await pilot.pause()
        assert inspector.calls == []  # Selection only shows metadata.
        await pilot.press("enter")
        await pilot.pause()
        assert "Export failed" in app.screen.query_one(TextArea).text
        await pilot.press("enter")
        await pilot.pause()
        preview = app.screen.query_one(TextArea)
        assert preview.text == "Preview [text]\n" * 100
        await pilot.press("tab", "pagedown")
        assert app.focused is preview
        assert preview.cursor_location[0] > 0
        await pilot.press("shift+tab", "j", "enter")
        assert "Symlink" in preview.text
        assert inspector.calls == [("app.bst", "file.txt")] * 2
        await pilot.press("i")
        assert preview.text == "Artifact overview"
        await pilot.press("escape")
        assert inspector.cancelled
        assert app.selected() == "app.bst"


async def test_artifact_pull_after_missing_artifact_and_retry():
    class PullInspector(FakeInspector):
        def load(self, name, section):
            raise ValueError("Artifact not cached")

        def pull_artifact(self, name):
            self.calls.append((name, "pull"))
            if len(self.calls) == 1:
                raise ValueError("Remote unavailable")
            return Inspection("Artifact pull completed", artifacts=[ArtifactEntry("new.txt", "reg 3 new.txt")])

    inspector = PullInspector()
    app = Explorer(graph=graph(), inspector_factory=lambda: inspector)
    async with app.run_test(size=(60, 18)) as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("a")
        await pilot.pause()
        button = app.screen.query_one("#pull-artifact", Button)
        assert button.display and not button.disabled
        assert "Artifact not cached" in app.screen.query_one(TextArea).text
        await pilot.press("p")
        await pilot.pause()
        assert "Remote unavailable" in app.screen.query_one(TextArea).text
        assert not button.disabled
        assert await pilot.click("#pull-artifact")
        await pilot.pause()
        files = app.screen.query_one(ArtifactTree)
        assert files.root.children[0].data.path == "new.txt"
        assert len(app.screen.query(ArtifactTree)) == 1
        assert inspector.calls == [("app.bst", "pull")] * 2
        await pilot.press("i")
        assert "Artifact pull completed" in app.screen.query_one(TextArea).text
        await pilot.press("escape", "b")
        await pilot.pause()
        assert not app.screen.query_one("#pull-artifact").display
        await pilot.press("p")
        assert len(inspector.calls) == 2


async def test_artifact_pull_cancellation_and_no_duplicate_command():
    import threading

    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    class PullInspector(FakeInspector):
        def pull_artifact(self, name):
            self.calls.append((name, "pull"))
            started.set()
            release.wait(timeout=5)
            finished.set()
            return Inspection("Late result", artifacts=[])

        def cancel(self):
            super().cancel()
            release.set()

    inspector = PullInspector()
    app = Explorer(graph=graph(), inspector_factory=lambda: inspector)
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert started.is_set()
        assert app.screen.query_one("#pull-artifact", Button).disabled
        await pilot.press("p", "p", "escape")
        await pilot.pause()
        assert finished.is_set() and inspector.cancelled
        assert inspector.calls.count(("app.bst", "pull")) == 1
        assert app.selected() == "app.bst"


async def test_pull_waits_for_preview_and_replaces_old_file_tree():
    import threading

    started, release = threading.Event(), threading.Event()

    class PullInspector(FakeInspector):
        def load(self, name, section):
            return Inspection("Old listing", artifacts=[ArtifactEntry("old.txt", "reg 3 old.txt")])

        def preview_artifact(self, name, path):
            self.calls.append("preview")
            started.set()
            release.wait(timeout=5)
            return "Old preview"

        def pull_artifact(self, name):
            self.calls.append("pull")
            return Inspection("Fresh listing", artifacts=[ArtifactEntry("new.txt", "reg 3 new.txt")])

        def cancel(self):
            super().cancel()
            release.set()

    inspector = PullInspector()
    app = Explorer(graph=graph(), inspector_factory=lambda: inspector)
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("a")
        await pilot.pause()
        files = app.screen.query_one(ArtifactTree)
        files.move_cursor(files.root.children[0])
        await pilot.press("enter")
        await pilot.pause()
        assert started.is_set()
        await pilot.press("p")
        await pilot.pause()
        assert inspector.calls == ["preview"]
        release.set()
        await pilot.pause()
        assert inspector.calls == ["preview", "pull"]
        assert app.screen.query_one(ArtifactTree) is not files
        assert "Old preview" not in app.screen.query_one(TextArea).text
        assert app.screen.query_one(ArtifactTree).root.children[0].data.path == "new.txt"
        assert not app.screen.query_one("#pull-artifact", Button).disabled


@pytest.mark.parametrize("finish", ["select", "info", "close"])
async def test_pending_artifact_preview_does_not_replace_new_selection(finish):
    import threading

    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    class ArtifactInspector(FakeInspector):
        def load(self, name, section):
            return Inspection("Artifact overview", artifacts=[
                ArtifactEntry("a", "-rw-r--r-- reg 1 a"),
                ArtifactEntry("b", "-rw-r--r-- reg 2 b"),
            ])

        def preview_artifact(self, name, path):
            started.set()
            release.wait(timeout=5)
            finished.set()
            return "Late preview"

        def cancel(self):
            super().cancel()
            release.set()

    inspector = ArtifactInspector()
    app = Explorer(graph=graph(), inspector_factory=lambda: inspector)
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root.children[0])
        await pilot.press("a")
        await pilot.pause()
        files = app.screen.query_one(ArtifactTree)
        files.move_cursor(files.root.children[0])
        await pilot.press("enter")
        await pilot.pause()
        assert started.is_set()
        assert "Loading preview" in app.screen.query_one(TextArea).text
        await pilot.press({"select": "j", "info": "i", "close": "escape"}[finish])
        release.set()
        await pilot.pause()
        assert finished.is_set()
        if finish == "close":
            assert inspector.cancelled
            assert app.selected() == "app.bst"
        else:
            preview = app.screen.query_one(TextArea).text
            assert "Late preview" not in preview
            assert ("reg 2 b" if finish == "select" else "Artifact overview") in preview


@pytest.mark.parametrize("scope,expected", [("all", {"app.bst", "compiler.bst"}), ("run", {"app.bst"})])
async def test_reverse_opens_consumers_in_selected_scope(scope, expected):
    app = Explorer(graph=graph())
    app.scope = scope
    async with app.run_test() as pilot:
        await app.reveal(["app.bst", "sub:lib.bst"])
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        root = app.query_one(Tree).root.children[0]
        assert root.is_expanded
        assert {child.data[-1] for child in root.children} == expected
        assert app.selected() == "sub:lib.bst"
        await pilot.press("r")
        await pilot.pause()
        assert app.reverse_root is None
        assert app.selected() == "sub:lib.bst"


async def test_reverse_without_consumers_explains_scope():
    app = Explorer(graph=graph())
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        tree.move_cursor(tree.root)
        await pilot.press("r")
        assert "Select an element" in str(app.query_one("#status", Static).render())
        tree.move_cursor(tree.root.children[0])
        await pilot.press("r")
        await pilot.pause()
        assert "No reverse dependencies" in str(app.query_one("#status", Static).render())
        assert "loaded graph" in str(app.query_one("#status", Static).render())
        assert app.selected() == "app.bst"
        await pilot.press("escape")
        await pilot.pause()
        assert app.reverse_root is None


async def test_source_browser_includes_dotfiles_and_hidden_directories(tmp_path):
    (tmp_path / ".config").mkdir()
    (tmp_path / ".config" / ".settings").write_text("hidden nested content")
    (tmp_path / ".env").write_text("hidden source content")
    app = Explorer(graph=graph(), inspector_factory=lambda: FakeInspector(workspace=tmp_path))
    async with app.run_test() as pilot:
        app.query_one(Tree).move_cursor(app.query_one(Tree).root.children[0])
        await pilot.press("s")
        await pilot.pause()
        files = app.screen.query_one(WorkspaceTree)
        await pilot.pause()
        entries = {child.data.path.name: child for child in files.root.children}
        assert set(entries) == {".config", ".env"}
        files.select_node(entries[".env"])
        await pilot.pause()
        assert app.screen.query_one(TextArea).text == "hidden source content"
        entries[".config"].expand()
        await pilot.pause()
        hidden = entries[".config"].children[0]
        assert hidden.data.path.name == ".settings"
        files.select_node(hidden)
        await pilot.pause()
        assert app.screen.query_one(TextArea).text == "hidden nested content"
