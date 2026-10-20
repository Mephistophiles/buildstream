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
"""Progressive disclosure for snapshot comparisons: overview, then one section's diff."""

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Select, Static, Tab, Tabs

from .explain import CATEGORY_LABELS, analyze, overview, sections, unified


def diff_text(content):
    """Explicit high-contrast diff colors, independent of a syntax theme."""
    result = Text()
    for line in content.splitlines():
        style = (
            "bold cyan" if line.startswith(("---", "+++", "@@")) else
            "bold #65d68b" if line.startswith("+") else
            "#ff8080" if line.startswith("-") else "dim"
        )
        result.append(line + "\n", style=style)
    return result


class DiffExplorer(App):
    TITLE = "bst-tree · compare"
    CSS = """
    Screen { background: $surface; }
    #summary { height: auto; padding: 0 1; color: $text-muted; }
    #warning { height: auto; max-height: 3; padding: 0 1; color: $warning; }
    #filters { height: 3; }
    #filters Tabs { width: 1fr; }
    #search { width: 28; }
    #body { layout: horizontal; height: 1fr; }
    #elements { width: 42%; border-right: solid $primary-muted; }
    #right { width: 1fr; }
    #selection { height: 2; padding: 0 1; text-style: bold; }
    #section { height: 3; }
    #diff { height: 1fr; padding: 0 1; }
    #body.compact { layout: vertical; }
    #body.compact #elements { width: 100%; height: 35%; border-right: none; }
    #body.compact #right { width: 100%; height: 1fr; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"), ("slash", "search", "Search"),
        ("0", "filter('all')", "All"), ("1", "filter('local')", "Inputs"),
        ("2", "filter('dependency')", "Deps"), ("3", "filter('unknown')", "Unexplained"),
        ("f", "sections", "Section"), ("c", "context", "More context"),
        Binding("j", "down", "Down", show=False), Binding("k", "up", "Up", show=False),
        Binding("escape", "back", "Elements", show=False),
    ]

    def __init__(self, old, new, *, fields=None, show_all=False, labels=("before", "after")):
        super().__init__()
        self.old, self.new = old, new
        self.fields = fields
        self.report = analyze(old, new, fields=fields, limit=100)
        self.records = {r["name"]: r for r in self.report["records"]}
        self.show_all = show_all
        self.labels = labels
        self.category = "local" if not show_all and self.report["summary"].get("local") else "all"
        self.selected_name = None
        self.full_context = False
        self.row_names = []

    def compose(self) -> ComposeResult:
        yield Header()
        counts = self.report["summary"]
        yield Static(
            f"{len(self.records)} changed elements  ·  "
            f"{counts.get('local', 0)} observed inputs  ·  "
            f"{counts.get('dependency', 0)} dependency candidates  ·  "
            f"{counts.get('unknown', 0)} unexplained", id="summary", markup=False,
        )
        warnings = self.report["warnings"]
        yield Static("\n".join(warnings[1:]) or "Dependency candidates indicate correlation; inspect evidence below.",
                     id="warning", markup=False)
        with Horizontal(id="filters"):
            yield Tabs(Tab("All", id="all"), Tab("Inputs", id="local"),
                       Tab("Dependencies", id="dependency"), Tab("Unexplained", id="unknown"), active=self.category)
            yield Input(placeholder="Filter element names…", id="search")
        with Container(id="body"):
            yield DataTable(id="elements", cursor_type="row")
            with Vertical(id="right"):
                yield Static("Snapshot context", id="selection", markup=False)
                yield Select([("Overview", "overview"), ("Snapshot context", "context")],
                             value="overview", allow_blank=False, id="section")
                yield RichLog(id="diff", wrap=True, markup=False, highlight=False, auto_scroll=False)
        yield Footer()

    def on_mount(self):
        self.sub_title = f"{self.labels[0]} → {self.labels[1]}"
        self.query_one(DataTable).add_columns("Element", "Evidence")
        self.populate()
        self.query_one(DataTable).focus()
        self.query_one("#body").set_class(self.size.width < 110, "compact")

    def on_resize(self, event):
        if self.is_mounted:
            self.query_one("#body").set_class(event.size.width < 110, "compact")

    def check_action(self, action, parameters):
        if isinstance(self.focused, Input) and action != "back":
            return False
        return super().check_action(action, parameters)

    def populate(self):
        table = self.query_one(DataTable)
        previous = self.selected_name
        table.clear()
        query = self.query_one(Input).value.casefold()
        priority = {"local": 0, "unknown": 1, "structural": 2, "dependency": 3, "unchanged": 4}
        names = set(self.records)
        if self.show_all and self.category == "all":
            names |= self.old.nodes.keys() | self.new.nodes.keys()
        self.row_names = sorted(
            (name for name in names if query in name.casefold()
             and (self.category == "all" or self.records[name]["category"] == self.category)),
            key=lambda name: (priority[self.records.get(name, {}).get("category", "unchanged")], name),
        )
        for name in self.row_names:
            r = self.records.get(name)
            label = CATEGORY_LABELS[r["category"]] if r else "Unchanged"
            if r and r["consumers"]:
                label += f" · {r['consumer_count']} consumers"
            table.add_row(name, label, key=name)
        if self.row_names:
            index = self.row_names.index(previous) if previous in self.row_names else 0
            table.move_cursor(row=index)
            self.choose(self.row_names[index])
        else:
            self.choose(None)

    @on(DataTable.RowHighlighted)
    def selected(self, event):
        name = event.row_key.value
        if name in self.row_names:
            self.choose(name)

    def choose(self, name):
        self.selected_name = name
        self.query_one("#selection", Static).update(name or "Snapshot context / no matching elements")
        options = [("Overview", "overview")]
        if name is not None:
            options += [("Diff · " + section, section)
                        for section in sections(self.old, self.new, name, self.fields)]
            record = self.records.get(name)
            if record and record["candidates"]:
                options.append(("Dependency candidates", "candidates"))
            if record and record["consumers"]:
                options.append(("Changed consumers", "consumers"))
        options.append(("Snapshot context", "context"))
        select = self.query_one(Select)
        select.set_options(options)
        select.value = "overview"
        self.render_detail()

    @on(Select.Changed)
    def section_changed(self, event):
        if event.value is not Select.BLANK:
            self.render_detail()

    def render_detail(self):
        log = self.query_one(RichLog)
        log.clear()
        section = self.query_one(Select).value
        name = self.selected_name
        record = self.records.get(name)
        if section == "overview":
            if record:
                log.write(overview(record))
                changed_sections = sections(self.old, self.new, name, self.fields)
                # Show one small diff immediately; hashes stay behind the section picker.
                first = next((s for s in changed_sections if s != "key"), None)
                if first:
                    log.write("\nChanged section: " + first + " · f selects another section\n")
                    content = unified(self.old, self.new, name, first, fields=self.fields)
                    lines = content.splitlines()
                    log.write(diff_text("\n".join(lines[:45])))
                    if len(lines) > 45:
                        log.write("… Select this section for the full diff.")
            else:
                log.write("No changes in the selected comparison." if name else "No matching changed elements.")
                log.write("\n" + "\n".join(self.report["warnings"]))
                log.write(diff_text(unified(self.old, self.new, None, "context")))
        elif section in ("candidates", "consumers") and record:
            if section == "candidates":
                log.write(f"Build input candidates: {record['candidate_count']} (showing {len(record['candidates'])}).\n"
                          "Causality is not proven. Full lists: --explain --format json.\n")
                for candidate in record["candidates"]:
                    log.write(candidate["name"] + " — " + candidate["reason"])
            else:
                log.write(f"Changed consumers: {record['consumer_count']} (showing {len(record['consumers'])}).\n"
                          "Full lists: --explain --format json.\n")
                for consumer in record["consumers"]:
                    log.write(consumer)
        elif section == "context":
            log.write("\n".join(self.report["warnings"]) + "\n")
            log.write(diff_text(unified(self.old, self.new, None, "context", full=self.full_context)))
        elif name is not None and section is not Select.BLANK:
            log.write(diff_text(unified(self.old, self.new, name, section, full=self.full_context, fields=self.fields)))

        log.scroll_home(animate=False)

    @on(Input.Changed)
    def search_changed(self, event):
        self.populate()

    @on(Tabs.TabActivated)
    def tab_changed(self, event):
        self.category = event.tab.id
        if self.is_mounted:
            self.populate()

    def action_filter(self, category):
        self.query_one(Tabs).active = category
        self.query_one(DataTable).focus()

    def action_search(self):
        self.query_one(Input).focus()

    def action_sections(self):
        self.query_one(Select).focus()
        self.query_one(Select).action_show_overlay()

    def action_context(self):
        self.full_context = not self.full_context
        self.notify("Full context" if self.full_context else "3 lines of context")
        self.render_detail()

    def action_back(self):
        self.query_one(DataTable).focus()

    def action_down(self):
        self.query_one(DataTable).action_cursor_down()

    def action_up(self):
        self.query_one(DataTable).action_cursor_up()
