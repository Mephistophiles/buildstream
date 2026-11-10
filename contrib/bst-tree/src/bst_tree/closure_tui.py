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
"""Standalone terminal UI for comparing dependency closures and declared edges."""

from rich.text import Text
from textual import on
from textual.app import App
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import DataTable, Footer, Input, RichLog, Select, Static, Tab, Tabs

from .semantic import CATEGORIES, LABELS, role_label

MARKS = {"added": "+", "removed": "-", "role_changed": "~", "retained": "=", "unchanged": "·"}
COLORS = {"added": "green", "removed": "red", "role_changed": "yellow", "retained": "cyan", "unchanged": "dim"}


class ClosureDiffApp(App):
    """Keep closure effects and exact edge changes visible as separate views."""

    TITLE = "bst-closure-diff"
    CSS = """
    #snapshots { height: 1; padding: 0 1; color: $accent; text-style: bold; }
    #selectors, #filters { height: 3; }
    #target, #search { width: 1fr; }
    #scope, #category { width: 22; }
    #views { height: 2; padding: 0 1; }
    #summary, #coverage { height: auto; max-height: 2; padding: 0 1; color: $text-muted; }
    #coverage { color: $warning; }
    #body { height: 1fr; layout: horizontal; }
    #results { width: 58%; min-height: 4; border-right: solid $primary-muted; }
    #detail { width: 1fr; min-height: 4; padding: 0 1; }
    #body.compact { layout: vertical; }
    #body.compact #results { width: 100%; height: 40%; border-right: none; }
    #body.compact #detail { width: 100%; height: 1fr; border-top: solid $primary-muted; }
    """
    BINDINGS = [
        Binding("t", "select('target')", "Target"),
        Binding("s", "select('scope')", "Scope"),
        Binding("f", "select('category')", "Filter"),
        Binding("e", "toggle_view", "Edges/closure"),
        Binding("slash", "search", "Search", key_display="/"),
        Binding("q", "quit", "Quit"),
        Binding("escape", "focus_results", "Results", show=False),
        Binding("j", "row('down')", "Down", show=False),
        Binding("k", "row('up')", "Up", show=False),
        Binding("enter", "detail", "Details", show=False, priority=True),
    ]

    def __init__(self, model, *, scope="all", show_all=False, labels=("before", "after")):
        super().__init__()
        self.model = model
        self.initial_scope = scope
        self.show_all = show_all
        self.labels = labels
        self._view_ready = False
        self._source_key = None
        self._records = []
        self._visible = []
        self._columns = None
        self._selected = None

    def compose(self):
        yield Static(f"bst-closure-diff   {self.labels[0]} → {self.labels[1]}", id="snapshots", markup=False)
        with Horizontal(id="selectors"):
            targets = self.model.targets
            yield Select(
                [(f"Target  {name}", name) for name in targets] or [("No common target", "")],
                value=targets[0] if targets else "",
                allow_blank=False,
                disabled=not targets,
                id="target",
            )
            yield Select(
                [("Build + runtime", "all"), ("Runtime", "run"), ("Build", "build")],
                value=self.initial_scope,
                allow_blank=False,
                id="scope",
            )
        yield Tabs(Tab("Closure effects", id="closure"), Tab("Declared edges", id="edges"), id="views")
        with Horizontal(id="filters"):
            categories = [c for c in CATEGORIES if self.show_all or c != "unchanged"]
            yield Select(
                [("All changes" if not self.show_all else "All dependencies", "all")]
                + [(LABELS[c], c) for c in categories],
                value="all",
                allow_blank=False,
                id="category",
            )
            yield Input(placeholder="Search names…", id="search")
        yield Static(id="summary", markup=False)
        coverage = [
            f"Only before: {', '.join(self.model.targets_removed)}" if self.model.targets_removed else "",
            f"Only after: {', '.join(self.model.targets_added)}" if self.model.targets_added else "",
        ]
        if any(coverage):
            yield Static("   ·   ".join(part for part in coverage if part), id="coverage", markup=False)
        with Container(id="body"):
            yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
            yield RichLog(id="detail", min_width=1, wrap=True, markup=False, highlight=False, auto_scroll=False)
        yield Footer()

    def on_mount(self):
        self._view_ready = True
        self._resize_body(self.size.width)
        self.refresh_results()
        self.query_one("#results", DataTable).focus()

    def on_resize(self, event):
        if self._view_ready:
            self._resize_body(event.size.width)

    def _resize_body(self, width):
        self.query_one("#body").set_class(width < 110, "compact")

    def check_action(self, action, parameters):
        if isinstance(self.focused, Input) and action != "focus_results":
            return False
        if action in ("detail", "row") and not isinstance(self.focused, DataTable):
            return False
        return super().check_action(action, parameters)

    def refresh_results(self):
        if not self._view_ready:
            return
        target = self.query_one("#target", Select).value
        scope = self.query_one("#scope", Select)
        category = self.query_one("#category", Select)
        edges = self.query_one(Tabs).active == "edges"
        scope.disabled = category.disabled = edges or not target
        key = target, None if edges else scope.value
        if key != self._source_key:
            self._source_key = key
            self._selected = None
            if not target:
                self._records = []
            elif edges:
                self._records = self.model.declared_changes(target)
            else:
                self._records = self.model.changes(target, scope.value, show_all=self.show_all)

        search = self.query_one(Input).value.casefold()
        visible = []
        table = self.query_one(DataTable)
        table.clear(columns=self._columns != edges)
        if self._columns != edges:
            self._columns = edges
            table.add_column("Op", width=2)
            table.add_column("Before", width=9)
            table.add_column("After", width=9)
            table.add_column("Consumer → Dependency" if edges else "Dependency")
        selected_index = 0
        for record in self._records:
            label = f"{record['from']} → {record['to']}" if edges else record["name"]
            if search not in label.casefold() or (not edges and category.value not in ("all", record["change"])):
                continue
            if record is self._selected:
                selected_index = len(visible)
            visible.append(record)
            change = record["change"]
            before, after = record["old"], record["new"]
            table.add_row(
                Text(MARKS[change], style=COLORS[change]),
                Text(role_label(before), style="red" if before != after and before else ""),
                Text(role_label(after), style="green" if before != after and after else ""),
                Text(label),
            )
        self._visible = visible
        if edges:
            summary = f"{len(visible)}/{len(self._records)} declared edge changes · all dependency types"
        elif target:
            counts = self.model.masks(target, scope.value)
            summary = (
                f"{len(visible)}/{len(self._records)} shown   +{counts['added'].bit_count()} added   "
                f"-{counts['removed'].bit_count()} removed   ~{counts['role_changed'].bit_count()} roles   "
                f"={counts['retained'].bit_count()} routes"
            )
        else:
            summary = "The snapshots have no common requested target"
        self.query_one("#summary", Static).update(summary)
        if visible:
            table.move_cursor(row=selected_index)
        self.render_selection()

    @on(Select.Changed)
    @on(Tabs.TabActivated)
    @on(Input.Changed)
    def filters_changed(self):
        self.refresh_results()

    @on(DataTable.RowHighlighted)
    def render_selection(self):
        table = self.query_one(DataTable)
        detail = self.query_one(RichLog)
        if table.cursor_row >= len(self._visible):
            self._selected = None
            detail.clear()
            detail.write("No matching rows" if self._records else "No changes in this view")
            return
        record = self._visible[table.cursor_row]
        if record is self._selected:
            return
        self._selected = record
        detail.clear()
        before, after = (role_label(record[side]) for side in ("old", "new"))
        if "from" in record:
            detail.write(Text(f"{record['from']} → {record['to']}", style="bold"))
            detail.write(f"Declared types   {before} → {after}")
            target, _ = self._source_key
            if record["to"] != target:
                roles = self.model.roles(target, record["to"])
                detail.write(f"Closure roles    {role_label(roles[0])} → {role_label(roles[1])}")
            return

        target, scope = self._source_key
        name = record["name"]
        detail.write(Text(name, style="bold"))
        detail.write(Text(LABELS[record["change"]], style=COLORS[record["change"]]))
        detail.write(f"Roles in scope   {before} → {after}")
        if scope != "all":
            full_before, full_after = self.model.roles(target, name)
            detail.write(f"All roles        {role_label(full_before)} → {role_label(full_after)}")

        for role in ("run", "build"):
            if role in record["old"] or role in record["new"]:
                detail.write(Text(f"{role.capitalize()} witness paths", style="bold"))
                for side, label in enumerate(("Before", "After")):
                    path = self.model.path(side, target, name, role)
                    detail.write(f"  {label}  " + (" → ".join(path) if path else "unreachable"))

        routes = self.model.route_changes(target, name, scope)
        if routes:
            detail.write(Text(f"Changed route edges ({len(routes)})", style="bold"))
            for edge in routes[:50]:
                types = f"{role_label(edge['old'])} → {role_label(edge['new'])}"
                detail.write(f"  {edge['from']} → {edge['to']}   [{types}]")
            if len(routes) > 50:
                detail.write(f"  … {len(routes) - 50} more. Search in Declared edges.")

    def action_select(self, widget_id):
        select = self.query_one(f"#{widget_id}", Select)
        if not select.disabled:
            select.focus()
            select.action_show_overlay()

    def action_toggle_view(self):
        tabs = self.query_one(Tabs)
        tabs.active = "closure" if tabs.active == "edges" else "edges"
        self.query_one(DataTable).focus()

    def action_search(self):
        self.query_one(Input).focus()

    def action_focus_results(self):
        self.query_one(DataTable).focus()

    def action_row(self, direction):
        table = self.query_one(DataTable)
        if direction == "down":
            table.action_cursor_down()
        else:
            table.action_cursor_up()

    def action_detail(self):
        self.query_one(RichLog).focus()
