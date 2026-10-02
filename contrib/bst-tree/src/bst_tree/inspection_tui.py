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
"""Focused screens keep element actions separate from graph navigation."""

import asyncio

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Footer, Static, TextArea, Tree

from .inspection import preview_file


class ElementMenu(ModalScreen):
    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("a", "choose('artifacts')", "Artifacts"),
        ("b", "choose('build')", "Build"),
        ("c", "choose('sources')", "Sources"),
    ]
    DEFAULT_CSS = """
    ElementMenu { align: center middle; }
    ElementMenu > VerticalScroll {
        width: 60; max-width: 100%; height: auto; max-height: 100%;
        padding: 1 2; border: round $accent; background: $surface;
    }
    ElementMenu Button { width: 100%; margin-top: 1; }
    """

    def __init__(self, name, available):
        super().__init__()
        self.name_label = name
        self.available = available

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(self.name_label, markup=False)
            if not self.available:
                yield Static("Live project required; snapshots contain graph metadata only.")
            yield Button("Artifacts [a]", id="artifacts", disabled=not self.available)
            yield Button("Build instructions [b]", id="build", disabled=not self.available)
            yield Button("Sources / workspace [c]", id="sources", disabled=not self.available)
            yield Button("Close", id="close")

    @on(Button.Pressed)
    def choose(self, event):
        event.stop()
        self.dismiss(None if event.button.id == "close" else event.button.id)

    def action_choose(self, section):
        if self.available:
            self.dismiss(section)


class WorkspaceTree(DirectoryTree):
    def filter_paths(self, paths):
        # Avoid traversing symlinked directories (including cycles/external trees).
        return [path for path in paths if not path.is_symlink()]


class InspectionScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Back"), ("q", "close", "Back")]
    DEFAULT_CSS = """
    InspectionScreen { background: $surface; }
    InspectionScreen #inspection-title { height: auto; padding: 1; }
    InspectionScreen #inspection-body { height: 1fr; }
    InspectionScreen #workspace { width: 35%; }
    InspectionScreen #content { width: 1fr; }
    """

    def __init__(self, name, section, inspector):
        super().__init__()
        self.element_name = name
        self.section = section
        self.inspector = inspector
        self.workspace = None
        self.preview_generation = 0
        self.closed = False

    def compose(self) -> ComposeResult:
        yield Static(f"{self.element_name} — {self.section}", id="inspection-title", markup=False)
        with Horizontal(id="inspection-body"):
            yield TextArea("Loading…", read_only=True, soft_wrap=False, id="content")
        yield Footer()

    def on_mount(self):
        self.query_one(TextArea).focus()
        self.run_worker(self.load())

    async def load(self):
        try:
            result = await asyncio.to_thread(self.inspector.load, self.element_name, self.section)
            if self.closed:
                return
            self.query_one(TextArea).load_text(result.text)
            if result.workspace:
                self.workspace = result.workspace
                tree = WorkspaceTree(result.workspace, id="workspace")
                await self.query_one("#inspection-body").mount(tree, before=self.query_one(TextArea))
                tree.focus()
        except Exception as error:
            if not self.closed:
                self.query_one(TextArea).load_text(f"Unable to load {self.section}:\n{error}")

    @on(Tree.NodeHighlighted)
    @on(Tree.NodeExpanded)
    @on(Tree.NodeSelected)
    def tree_event(self, event):
        event.stop()

    @on(DirectoryTree.FileSelected)
    async def file_selected(self, event):
        event.stop()
        self.preview_generation += 1
        generation = self.preview_generation

        async def read():
            try:
                content = await asyncio.to_thread(preview_file, self.workspace, event.path)
            except (OSError, ValueError) as error:
                content = f"Unable to preview file: {error}"
            if not self.closed and generation == self.preview_generation:
                self.query_one("#inspection-title", Static).update(f"{self.element_name} — {event.path}")
                self.query_one(TextArea).load_text(content)

        self.run_worker(read())

    def action_close(self):
        self.closed = True
        self.inspector.cancel()
        self.dismiss()

    def on_unmount(self):
        self.closed = True
        self.inspector.cancel()
