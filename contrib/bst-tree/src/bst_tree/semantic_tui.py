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
"""Target-focused dependency effects, with lazy before/after witness paths."""

import json

from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Select, Static, Tab, Tabs

from .semantic import LABELS, NOTE, role_label


class SemanticScreen(Screen):
    AUTO_FOCUS = "#sem-elements"
    CSS = """
    SemanticScreen { background: $surface; }
    #sem-controls { height: 3; }
    #sem-target { width: 1fr; }
    #sem-scope { width: 24; }
    #sem-summary { height: auto; padding: 0 1; color: $text-muted; }
    #sem-filters { height: 3; }
    #sem-filters Tabs { width: 1fr; }
    #sem-search { width: 28; }
    #sem-body { height: 1fr; layout: horizontal; }
    #sem-elements { width: 42%; border-right: solid $primary-muted; }
    #sem-right { width: 1fr; }
    #sem-selection { height: 2; padding: 0 1; text-style: bold; }
    #sem-section { height: 3; }
    #sem-detail { height: 1fr; padding: 0 1; }
    #sem-body.compact { layout: vertical; }
    #sem-body.compact #sem-elements { width: 100%; height: 30%; border-right: none; }
    #sem-body.compact #sem-right { width: 100%; height: 1fr; }
    """
    BINDINGS = [
        ("escape", "back", "Return"), ("q", "quit", "Quit"),
        ("slash", "search", "Search"), ("1", "filter('effects')", "Effects"),
        ("2", "filter('retained')", "Still reachable"), ("0", "filter('all')", "All"),
        ("f", "sections", "Section"), ("t", "target", "Target"), ("s", "scope", "Scope"),
        Binding("j", "down", "Down", show=False), Binding("k", "up", "Up", show=False),
        Binding("m", "back", "Return", show=False),
    ]

    def __init__(self, model, *, scope="all", show_all=False):
        super().__init__()
        self.model = model
        self.scope = scope
        self.show_all = show_all
        self.target = model.targets[0] if model.targets else ""
        self.category = "effects"
        self.selected_name = None
        self.row_names = []
        self.records = {}
        self.ready = False

    def compose(self):
        yield Header()
        with Horizontal(id="sem-controls"):
            options = [("Target · " + t, t) for t in self.model.targets] or [("No common requested targets", "")]
            yield Select(options, value=self.target, allow_blank=False,
                         disabled=not self.model.targets, id="sem-target")
            yield Select([("Build + runtime", "all"), ("Runtime only", "run"), ("Build only", "build")],
                         value=self.scope, allow_blank=False, id="sem-scope")
        yield Static("Dependency effects", id="sem-summary", markup=False)
        with Horizontal(id="sem-filters"):
            yield Tabs(Tab("Effects", id="sem-effects"), Tab("Still reachable", id="sem-retained"),
                       Tab("All", id="sem-all"))
            yield Input(placeholder="Filter dependencies…", id="sem-search")
        with Container(id="sem-body"):
            yield DataTable(id="sem-elements", cursor_type="row")
            with Vertical(id="sem-right"):
                yield Static("Select a dependency", id="sem-selection", markup=False)
                yield Select([("Overview", "overview"), ("Witness paths", "paths"),
                              ("Changed routes", "routes"), ("Snapshot context", "context")],
                             value="overview", allow_blank=False, id="sem-section")
                yield RichLog(id="sem-detail", wrap=True, markup=False, highlight=False, auto_scroll=False)
        yield Footer()

    def on_mount(self):
        self.query_one(DataTable).add_columns("Dependency", "Effect")
        self.ready = True
        self.populate(reset_category=True)
        self.query_one(DataTable).focus()
        self.query_one("#sem-body").set_class(self.app.size.width < 110, "compact")

    def on_resize(self, event):
        if self.ready:
            self.query_one("#sem-body").set_class(event.size.width < 110, "compact")

    def check_action(self, action, parameters):
        if isinstance(self.focused, Input) and action != "back":
            return False
        return super().check_action(action, parameters)

    def populate(self, *, reset_category=False):
        if not self.ready:
            return
        previous = self.selected_name
        table = self.query_one(DataTable)
        table.clear()
        self.records = {r["name"]: r for r in self.model.changes(self.target, self.scope, show_all=self.show_all)} if self.target else {}
        counts = {c: mask.bit_count() for c, mask in self.model.masks(self.target, self.scope).items()} if self.target else {}
        self.query_one("#sem-summary", Static).update(
            f"+{counts.get('added', 0)} added  ·  -{counts.get('removed', 0)} removed  ·  "
            f"~{counts.get('role_changed', 0)} roles  ·  ={counts.get('retained', 0)} still reachable"
        )
        if reset_category:
            effects = sum(counts.get(c, 0) for c in ("added", "removed", "role_changed"))
            self.category = "retained" if not effects and counts.get("retained") else "effects"
            self.query_one(Tabs).active = "sem-" + self.category
        query = self.query_one(Input).value.casefold()
        self.row_names = [name for name, r in self.records.items() if query in name.casefold() and (
            self.category == "all" or self.category == "retained" and r["change"] == "retained"
            or self.category == "effects" and r["change"] in ("added", "removed", "role_changed")
        )]
        for name in self.row_names:
            record = self.records[name]
            mark = {"added": "+", "removed": "-", "role_changed": "~", "retained": "=", "unchanged": " "}[record["change"]]
            table.add_row(name, f"{mark} {role_label(record['old'])} → {role_label(record['new'])}", key=name)
        if self.row_names:
            index = self.row_names.index(previous) if previous in self.row_names else 0
            table.move_cursor(row=index)
            self.choose(self.row_names[index])
        else:
            self.choose(None)

    @on(DataTable.RowHighlighted)
    def selected(self, event):
        event.stop()
        if event.row_key.value in self.row_names:
            self.choose(event.row_key.value)

    def choose(self, name):
        self.selected_name = name
        self.query_one("#sem-selection", Static).update(name or "Target summary / no matching dependencies")
        self.query_one("#sem-section", Select).value = "overview"
        self.render_detail()

    def write_paths(self, log, record, *, full=False):
        for role in ("build", "run"):
            if role not in record["old"] and role not in record["new"]:
                continue
            before, after = (self.model.path(side, self.target, record["name"], role) for side in (0, 1))
            def label(path):
                if path is None:
                    return "not reachable"
                if not full and len(path) > 8:
                    path = path[:3] + ["…"] + path[-3:]
                return " → ".join(path)
            if before == after:
                log.write(Text(f"= {role}: {label(before)}", style="cyan"))
            else:
                log.write(Text(f"- {role}: {label(before)}", style="#ff8080"))
                log.write(Text(f"+ {role}: {label(after)}", style="#65d68b"))

    def render_detail(self):
        if not self.ready:
            return
        log = self.query_one(RichLog)
        log.clear()
        section = self.query_one("#sem-section", Select).value
        record = self.records.get(self.selected_name)
        if section == "context":
            log.write(NOTE + "\n")
            for side, graph in zip(("Before", "After"), self.model.graphs):
                log.write(side + "\n" + json.dumps({"targets": graph.targets, "metadata": graph.metadata},
                                                   ensure_ascii=False, sort_keys=True, indent=2))
        elif record is None:
            log.write("No matching dependencies in this view.\n" if self.target else "No common requested targets.\n")
            log.write("Targets requested on only one side are not interpreted as dependency additions/removals.")
            if self.model.targets_added:
                log.write("Targets only in new: " + ", ".join(self.model.targets_added))
            if self.model.targets_removed:
                log.write("Targets only in old: " + ", ".join(self.model.targets_removed))
            log.write("\n" + NOTE)
        elif section == "routes":
            routes = self.model.route_changes(self.target, record["name"], self.scope)
            log.write(f"Changed effective routes: {len(routes)} (showing up to 100).\n")
            for edge in routes[:100]:
                log.write(f"{edge['from']} → {edge['to']}\n"
                          f"  {role_label(edge['old'])} → {role_label(edge['new'])}; "
                          f"affects {role_label(edge['roles'])}")
        else:
            log.write(LABELS[record["change"]] + "\n")
            log.write(f"Roles: {role_label(record['old'])} → {role_label(record['new'])}\n")
            self.write_paths(log, record, full=section == "paths")
            if record["change"] == "retained":
                log.write("\nDeclared routes changed; this dependency remains reachable with the same roles. "
                          "One shortest witness is shown per role; it may stay unchanged when another route changes.")
            if section == "overview":
                log.write("\nf: full witness paths / changed routes · Escape: return to input changes")
        log.scroll_home(animate=False)

    @on(Select.Changed, "#sem-target")
    @on(Select.Changed, "#sem-scope")
    def comparison_changed(self, event):
        event.stop()
        if event.value is Select.BLANK or not self.ready:
            return
        self.target = self.query_one("#sem-target", Select).value
        self.scope = self.query_one("#sem-scope", Select).value
        self.populate(reset_category=True)

    @on(Select.Changed, "#sem-section")
    def section_changed(self, event):
        event.stop()
        if event.value is not Select.BLANK:
            self.render_detail()

    @on(Tabs.TabActivated)
    def tab_changed(self, event):
        event.stop()
        if event.tab.id != self.query_one(Tabs).active:
            return
        self.category = event.tab.id.removeprefix("sem-")
        self.populate()

    @on(Input.Changed)
    def search_changed(self, event):
        event.stop()
        self.populate()

    def action_filter(self, category):
        self.query_one(Tabs).active = "sem-" + category
        self.query_one(DataTable).focus()

    def action_search(self):
        self.query_one(Input).focus()

    def open_select(self, selector):
        select = self.query_one(selector, Select)
        select.focus()
        if not select.disabled:
            select.action_show_overlay()

    def action_sections(self):
        self.open_select("#sem-section")

    def action_target(self):
        self.open_select("#sem-target")

    def action_scope(self):
        self.open_select("#sem-scope")

    def action_down(self):
        self.query_one(DataTable).action_cursor_down()

    def action_up(self):
        self.query_one(DataTable).action_cursor_up()

    def action_back(self):
        if isinstance(self.focused, Input):
            self.query_one(DataTable).focus()
        else:
            self.app.pop_screen()

    def action_quit(self):
        self.app.exit()
