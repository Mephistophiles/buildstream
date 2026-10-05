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
from pathlib import PurePosixPath

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
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
        Binding("up", "app.focus_previous", "Previous", show=False, priority=True),
        Binding("down", "app.focus_next", "Next", show=False, priority=True),
    ]
    DEFAULT_CSS = """
    ElementMenu { align: center middle; }
    ElementMenu > VerticalScroll {
        width: 60; max-width: 100%; height: auto; max-height: 100%;
        padding: 0 2; border: round $accent; background: $surface;
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
            yield Static("Press a / b / c, or ↑↓ then Enter", markup=False)
            if not self.available:
                yield Static("Live project required; snapshots contain graph metadata only.")
            for key, label, section in (
                ("a", "Artifacts", "artifacts"),
                ("b", "Build instructions", "build"),
                ("c", "Sources / workspace", "sources"),
            ):
                yield Button(
                    Text.from_markup(f"[bold reverse] {key} [/bold reverse]  {label}"),
                    id=section,
                    disabled=not self.available,
                )
            yield Button("Esc  Close", id="close")
        yield Footer()

    @on(Button.Pressed)
    def choose(self, event):
        event.stop()
        self.dismiss(None if event.button.id == "close" else event.button.id)

    def action_choose(self, section):
        if self.available:
            self.dismiss(section)


class FileNavigation:
    def action_open_directory(self):
        node = self.cursor_node
        if node and node.allow_expand:
            if node.is_expanded and node.children:
                self.move_cursor(node.children[0])
            else:
                node.expand()

    def action_parent_directory(self):
        node = self.cursor_node
        if node:
            if node.is_expanded:
                node.collapse()
            elif node.parent:
                self.move_cursor(node.parent)


FILE_BINDINGS = [
    Binding("right", "open_directory", "Open directory", show=False),
    Binding("left", "parent_directory", "Parent", show=False),
]


class WorkspaceTree(FileNavigation, DirectoryTree):
    BINDINGS = FILE_BINDINGS

    def filter_paths(self, paths):
        # Avoid traversing symlinked directories (including cycles/external trees).
        return [path for path in paths if not path.is_symlink()]


class ArtifactTree(FileNavigation, Tree):
    BINDINGS = FILE_BINDINGS

    def __init__(self, entries):
        super().__init__("Artifact files", id="artifact-files")
        nodes = {(): self.root}
        for entry in entries:
            parts = PurePosixPath(entry.path).parts
            for index, part in enumerate(parts):
                key = parts[: index + 1]
                if key not in nodes:
                    nodes[key] = nodes[key[:-1]].add(Text(part), allow_expand=True)
                if index == len(parts) - 1:
                    nodes[key].data = entry
                    nodes[key].allow_expand = entry.directory
        self.root.expand()


class InspectionScreen(ModalScreen):
    BINDINGS = [
        ("escape", "close", "Back"),
        Binding("q", "close", "Back", show=False),
        ("f", "fetch_sources", "Load sources"),
        ("i", "information", "Info"),
        Binding("tab", "app.focus_next", "Switch pane"),
    ]
    DEFAULT_CSS = """
    InspectionScreen { background: $surface; }
    InspectionScreen #inspection-title { height: auto; padding: 1; }
    InspectionScreen #inspection-body { height: 1fr; }
    InspectionScreen #workspace, InspectionScreen #artifact-files { width: 45%; }
    InspectionScreen #content { width: 1fr; }
    InspectionScreen #inspection-help { height: auto; }
    InspectionScreen #load-sources { display: none; }
    """

    def __init__(self, name, section, inspector):
        super().__init__()
        self.element_name = name
        self.section = section
        self.inspector = inspector
        self.workspace = None
        self.preview_generation = 0
        self.closed = False
        self.can_checkout = False
        self.loading_sources = False
        self.information = ""

    def compose(self) -> ComposeResult:
        yield Static(f"{self.element_name} — {self.section}", id="inspection-title", markup=False)
        help_text = "↑↓ / PgUp / PgDn: scroll"
        if self.section != "build":
            help_text = "Tab: switch pane · ↑↓: select · ←→: folders · i: info"
        if self.section == "sources":
            help_text += " · Enter: preview"
        yield Static(help_text, id="inspection-help", markup=False)
        yield Button("f  Load source files (may download)", id="load-sources")
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
            self.information = result.text
            self.query_one(TextArea).load_text(result.text)
            self.can_checkout = result.can_checkout
            self.query_one("#load-sources").display = result.can_checkout
            self.refresh_bindings()
            if result.workspace:
                await self.mount_files(result.workspace)
            elif result.artifacts is not None:
                tree = ArtifactTree(result.artifacts)
                await self.query_one("#inspection-body").mount(tree, before=self.query_one(TextArea))
                tree.focus()
        except Exception as error:
            if not self.closed:
                self.query_one(TextArea).load_text(f"Unable to load {self.section}:\n{error}")

    async def mount_files(self, path):
        self.workspace = path
        tree = WorkspaceTree(path, id="workspace")
        await self.query_one("#inspection-body").mount(tree, before=self.query_one(TextArea))
        tree.focus()

    def check_action(self, action, parameters):
        if action == "fetch_sources":
            return self.can_checkout and not self.loading_sources
        return True

    @on(Button.Pressed, "#load-sources")
    def load_sources_pressed(self, event):
        event.stop()
        self.action_fetch_sources()

    def action_fetch_sources(self):
        if not self.can_checkout or self.loading_sources:
            return
        self.loading_sources = True
        self.query_one("#load-sources", Button).disabled = True
        self.refresh_bindings()
        self.query_one(TextArea).load_text(
            "Loading source files from cache / upstream…\nEsc cancels and returns to the tree."
        )

        async def checkout():
            try:
                result = await asyncio.to_thread(self.inspector.checkout_sources, self.element_name)
                if self.closed:
                    return
                self.information += "\n\n" + result.text
                self.query_one(TextArea).load_text(self.information)
                self.can_checkout = False
                self.query_one("#load-sources").display = False
                await self.mount_files(result.workspace)
            except Exception as error:
                if not self.closed:
                    self.query_one(TextArea).load_text(
                        f"Unable to load source files:\n{error}\n\n{self.information}"
                    )
            finally:
                self.loading_sources = False
                if not self.closed:
                    self.query_one("#load-sources", Button).disabled = False
                    self.refresh_bindings()

        self.run_worker(checkout())

    def action_information(self):
        self.preview_generation += 1
        self.query_one("#inspection-title", Static).update(f"{self.element_name} — {self.section}")
        self.query_one(TextArea).load_text(self.information)

    @on(Tree.NodeHighlighted)
    @on(Tree.NodeExpanded)
    @on(Tree.NodeSelected)
    def tree_event(self, event):
        event.stop()
        if isinstance(event.control, ArtifactTree):
            if event.node.data:
                entry = event.node.data
                self.query_one("#inspection-title", Static).update(f"{self.element_name} — {entry.path}")
                self.query_one(TextArea).load_text(f"{entry.path}\n\n{entry.details}")
            else:
                self.action_information()

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
