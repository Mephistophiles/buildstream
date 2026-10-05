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
"""Lazy dependency tree; the graph remains independent of UI occurrences."""

import asyncio
import json

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, Static, Tree

from .inspection_tui import ElementMenu, InspectionScreen


class DependencyTree(Tree):
    BINDINGS = [("left", "app.left", "Parent"), ("right", "app.right", "Expand")]


class Explorer(App):
    TITLE = "bst-tree"
    GRAPH_ACTIONS = {
        "menu",
        "inspect",
        "toggle",
        "left",
        "right",
        "down",
        "up",
        "search",
        "next_match",
        "reverse",
        "why",
        "scope",
        "back",
    }
    CSS = """
    #body { height: 1fr; }
    #tree { width: 1fr; }
    #details { width: 45%; overflow-y: auto; padding: 1; }
    #search { display: none; }
    #status { height: auto; max-height: 5; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("m", "menu", "Element"),
        Binding("a", "inspect('artifacts')", "Artifacts", show=False),
        Binding("b", "inspect('build')", "Build", show=False),
        Binding("s", "inspect('sources')", "Sources", show=False),
        Binding("space", "toggle", "Expand", show=False),
        Binding("h", "left", "Parent", show=False),
        Binding("l", "right", "Expand", show=False),
        Binding("j", "down", "Down", show=False),
        Binding("k", "up", "Up", show=False),
        ("slash", "search", "Search"),
        Binding("n", "next_match(1)", "Next", show=False),
        Binding("N", "next_match(-1)", "Previous", show=False),
        ("r", "reverse", "Reverse"),
        ("w", "why", "Why"),
        Binding("S", "scope", "Scope", key_display="Shift+S"),
        ("escape", "back", "Back"),
    ]

    def __init__(self, graph=None, loader=None, cancel_loader=None, inspector_factory=None):
        super().__init__()
        self.graph = graph
        self.loader = loader
        self.cancel_loader = cancel_loader
        self.inspector_factory = inspector_factory
        self.scope = "all"
        self.reverse_root = None
        self.matches = []
        self.match_index = -1
        self.saved_tree = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search element names (Enter)", id="search")
        with Horizontal(id="body"):
            yield DependencyTree("Dependencies", id="tree")
            yield Static("Select an element", id="details", markup=False)
        yield Static("Loading… (q to cancel)", id="status", markup=False)
        yield Footer()

    def on_unmount(self):
        if self.cancel_loader:
            self.cancel_loader()

    def check_action(self, action, parameters):
        # Keep Textual's standard actions (including Tab/Shift+Tab) available
        # in every screen; only graph actions depend on the explorer's context.
        if action not in self.GRAPH_ACTIONS:
            return super().check_action(action, parameters)
        if len(self.screen_stack) > 1:
            return False
        if isinstance(self.focused, Input) and action != "back":
            return False
        return self.graph is not None

    async def on_mount(self):
        if self.graph is not None:
            self.rebuild()
        else:
            self.run_worker(self.load_graph(), exclusive=True)

    async def load_graph(self):
        try:
            self.graph = await asyncio.to_thread(self.loader)
            self.rebuild()
        except Exception as error:  # Display background loader failures in the terminal.
            self.query_one("#status", Static).update(f"Error: {error}")
            self.load_error = str(error)
            self.exit(2)

    def rebuild(self):
        self.forward_adjacency = self.graph.adjacency()
        self.view = self.graph.scoped(self.scope)
        self.adjacency = self.view.adjacency(reverse=self.reverse_root is not None)
        tree = self.query_one(Tree)
        tree.clear()
        self.query_one("#details", Static).update("Select an element")
        tree.root.set_label(f"{self.scope}: " + ("Reverse dependencies" if self.reverse_root else "Dependencies"))
        roots = [self.reverse_root] if self.reverse_root else self.view.targets
        for name in roots:
            if name in self.view.nodes:
                self.add_occurrence(tree.root, name, ())
        tree.root.expand()
        tree.focus()
        self.query_one("#status", Static).update(f"{len(self.view.nodes)} elements; scope={self.scope}")

    def add_occurrence(self, parent, name, path, kinds=None):
        label = name + (" [" + "/".join(sorted(kinds)) + "]" if kinds else "")
        cycle = name in path
        node = parent.add(
            Text(label + (" (cycle)" if cycle else "")),
            data=(*path, name),
            allow_expand=bool(self.adjacency[name]) and not cycle,
        )
        return node

    def is_current_node(self, node):
        tree = self.query_one("#tree", Tree)
        if node.tree is not tree:
            return False
        # clear() replaces the root but reuses the Tree widget and node IDs.
        # Queued events must belong to the current root, even if their element
        # name is still present in the new scope or direction.
        while node.parent is not None:
            node = node.parent
        return node is tree.root

    @on(Tree.NodeExpanded)
    def expanded(self, event):
        node = event.node
        if not self.is_current_node(node):
            return
        if node.data and not node.children and node.data[-1] not in node.data[:-1]:
            for child, kinds in self.adjacency[node.data[-1]]:
                self.add_occurrence(node, child, node.data, kinds)

    @on(Tree.NodeHighlighted)
    def highlighted(self, event):
        if not self.is_current_node(event.node):
            return
        if event.node.data:
            name = event.node.data[-1]
            data = self.graph.nodes[name]
            details = {
                "name": name,
                **data,
                "dependencies": [
                    {"name": child, "types": sorted(kinds)} for child, kinds in self.forward_adjacency[name]
                ],
            }
            self.query_one("#details", Static).update(json.dumps(details, ensure_ascii=False, indent=2))
        else:
            self.query_one("#details", Static).update("Select an element")

    def action_menu(self):
        name = self.selected()
        if name:
            self.push_screen(ElementMenu(name, self.inspector_factory is not None), self.open_inspection)
        else:
            self.query_one("#status", Static).update("Select an element first")

    def open_inspection(self, section):
        if section:
            self.action_inspect(section)

    def action_inspect(self, section):
        name = self.selected()
        if not name:
            self.query_one("#status", Static).update("Select an element first")
        elif self.inspector_factory is None:
            self.query_one("#status", Static).update("Live project required; snapshots contain graph metadata only")
        else:
            self.push_screen(InspectionScreen(name, section, self.inspector_factory()))

    def selected(self):
        node = self.query_one(Tree).cursor_node
        return node.data[-1] if node and node.data else None

    def action_toggle(self):
        node = self.query_one(Tree).cursor_node
        if node:
            node.toggle()

    def action_left(self):
        tree = self.query_one(Tree)
        node = tree.cursor_node
        if node and node.is_expanded:
            node.collapse()
        elif node and node.parent:
            tree.move_cursor(node.parent)

    def action_right(self):
        node = self.query_one(Tree).cursor_node
        if node:
            node.expand()

    def action_down(self):
        self.query_one(Tree).action_cursor_down()

    def action_up(self):
        self.query_one(Tree).action_cursor_up()

    def action_search(self):
        search = self.query_one(Input)
        search.display = True
        search.focus()

    @on(Input.Submitted)
    async def search_submitted(self, event):
        self.matches = sorted(name for name in self.view.nodes if event.value.casefold() in name.casefold())
        self.match_index = -1
        event.input.display = False
        self.query_one(Tree).focus()
        await self.action_next_match(1)

    async def action_next_match(self, step):
        if not self.matches:
            self.query_one("#status", Static).update("No search matches")
            return
        self.match_index = (self.match_index + step) % len(self.matches)
        if self.reverse_root:
            self.reverse_root = None
            self.rebuild()
        paths = self.view.paths_to(self.matches[self.match_index])
        if paths:
            await self.reveal(paths[0])
        self.query_one("#status", Static).update(f"Match {self.match_index + 1}/{len(self.matches)}")

    async def reveal(self, path):
        tree = self.query_one(Tree)
        node = tree.root
        for name in path:
            node.expand()
            # Populate synchronously too: queued expansion events are idempotent.
            if node.data and not node.children and node.data[-1] not in node.data[:-1]:
                for child, kinds in self.adjacency[node.data[-1]]:
                    self.add_occurrence(node, child, node.data, kinds)
            node = next(child for child in node.children if child.data[-1] == name)
        self.call_after_refresh(tree.move_cursor, node)
        self.call_after_refresh(tree.scroll_to_node, node)
        return node

    def action_reverse(self):
        if self.reverse_root:
            self.action_back()
            return
        name = self.selected()
        if name:
            tree = self.query_one(Tree)
            expanded = []
            stack = [tree.root]
            while stack:
                node = stack.pop()
                if node.data and node.is_expanded:
                    expanded.append(node.data)
                stack.extend(node.children)
            self.saved_tree = (expanded, tree.cursor_node.data)
            self.reverse_root = name
            self.rebuild()

    def action_back(self):
        search = self.query_one(Input)
        if search.display:
            search.display = False
            self.query_one(Tree).focus()
            return
        if self.reverse_root:
            self.reverse_root = None
            self.rebuild()
            self.run_worker(self.restore())
        self.query_one(Tree).focus()

    async def restore(self):
        if self.saved_tree:
            expanded, selected = self.saved_tree
            for path in sorted(expanded, key=len):
                node = await self.reveal(path)
                node.expand()
            await self.reveal(selected)

    def action_scope(self):
        if self.graph is not None:
            scopes = ["all", "run", "build"]
            self.scope = scopes[(scopes.index(self.scope) + 1) % len(scopes)]
            self.reverse_root = None
            self.matches = []
            self.rebuild()

    def action_why(self):
        name = self.selected()
        if name:
            paths = self.view.paths_to(name)
            lines = []
            for path in paths:
                line = path[0]
                for parent, child in zip(path, path[1:]):
                    line += f" --{'/'.join(sorted(self.view.edges[parent, child]))}--> {child}"
                lines.append(line)
            self.query_one("#details", Static).update("Paths from targets:\n" + "\n".join(lines))
