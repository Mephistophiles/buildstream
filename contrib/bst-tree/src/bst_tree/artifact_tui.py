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
"""File comparison first; expensive ABI and diffoscope analyses are explicit actions."""

import asyncio
import json

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Select, Static, Tab, Tabs

from .artifact_process import Cancelled
from .artifacts import FILE_KINDS
from .diff_tui import diff_text


class ArtifactDiffApp(App):
    TITLE = "bst-tree · artifacts"
    AUTO_FOCUS = "#artifact-files"
    CSS = """
    #artifact-summary { height: auto; padding: 0 1; color: $text-muted; }
    #artifact-filters { height: 3; }
    #artifact-filters Tabs { width: 1fr; }
    #artifact-search { width: 28; }
    #artifact-body { layout: horizontal; height: 1fr; }
    #artifact-files { width: 42%; border-right: solid $primary-muted; }
    #artifact-right { width: 1fr; }
    #artifact-selection { height: 2; padding: 0 1; text-style: bold; }
    #artifact-section { height: 3; }
    #artifact-content { height: 1fr; padding: 0 1; }
    #artifact-status { height: auto; max-height: 3; padding: 0 1; color: $text-muted; }
    #artifact-body.compact { layout: vertical; }
    #artifact-body.compact #artifact-files { width: 100%; height: 30%; border-right: none; }
    #artifact-body.compact #artifact-right { width: 100%; height: 1fr; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("a", "abidiff", "ABI"),
        ("b", "bloaty", "Size"),
        ("d", "diffoscope", "File detail"),
        Binding("D", "diffoscope_all", "Artifact detail", key_display="Shift+D"),
        ("slash", "search", "Search"),
        ("f", "sections", "Section"),
        Binding("j", "down", "Down", show=False),
        Binding("k", "up", "Up", show=False),
        Binding("escape", "back", "Back", show=False),
    ]

    def __init__(self, session, loader=None):
        super().__init__()
        self.session = session
        self.loader = loader or session.load
        self.ready = False
        self.closed = False
        self.busy = True
        self.ui_ready = False
        self.category = "all"
        self.selected_path = None
        self.row_names = []
        self.records = {}
        self.generation = 0
        self.io = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Loading artifact comparison…", id="artifact-summary", markup=False)
        with Horizontal(id="artifact-filters"):
            yield Tabs(
                Tab("All", id="af-all"),
                Tab("Content", id="af-content"),
                Tab("Metadata", id="af-metadata"),
                Tab("ELF", id="af-elf"),
                id="artifact-categories",
            )
            yield Input(placeholder="Filter file paths…", id="artifact-search")
        with Container(id="artifact-body"):
            yield DataTable(id="artifact-files", cursor_type="row")
            with Vertical(id="artifact-right"):
                yield Static("Select a file", id="artifact-selection", markup=False)
                yield Select(
                    [("Overview", "overview"), ("Context", "context")],
                    value="overview",
                    allow_blank=False,
                    id="artifact-section",
                )
                yield RichLog(id="artifact-content", wrap=True, markup=False, highlight=False, auto_scroll=False)
        yield Static("Exporting / hashing… q or Escape cancels", id="artifact-status", markup=False)
        yield Footer()

    def on_mount(self):
        self.ui_ready = True
        self.query_one(DataTable).add_columns("Path", "Change")
        self.query_one("#artifact-body").set_class(self.size.width < 110, "compact")
        self.run_worker(self.load())

    def on_resize(self, event):
        if self.ui_ready:
            self.query_one("#artifact-body").set_class(event.size.width < 110, "compact")

    def progress(self, text):
        if not self.closed:
            try:
                self.call_from_thread(self.status, text)
            except RuntimeError:
                pass

    def status(self, text):
        if not self.closed:
            self.query_one("#artifact-status", Static).update(text)

    async def load(self):
        try:
            async with self.io:
                await asyncio.to_thread(self.loader, self.progress)
            if self.closed:
                return
            self.ready = True
            self.busy = False
            report = self.session.report()
            self.records = {r["path"]: r for r in report["changes"]}
            counts = report["summary"]
            self.query_one("#artifact-summary", Static).update(
                f"+{counts['added']} added  ·  -{counts['removed']} removed  ·  "
                f"~{counts['modified']} modified  ·  {len(report['elf_pairs'])} changed ELF pairs"
            )
            self.populate()
            self.status("a: abidiff on ELF pair · d: diffoscope on file pair · Shift+D: whole archives")
            self.refresh_bindings()
        except Cancelled:
            if not self.closed:
                self.exit(130)
        except Exception as error:
            if not self.closed:
                self.load_error = str(error)
                self.exit(2)

    def check_action(self, action, parameters):
        if isinstance(self.focused, Input) and action != "back":
            return False
        if action in ("abidiff", "diffoscope", "bloaty", "diffoscope_all"):
            if not self.ready or self.busy:
                return False
            if action == "diffoscope_all":
                return True
            return bool(
                self.selected_path and self.session.pair(self.selected_path, elf=action in ("abidiff", "bloaty"))
            )
        return super().check_action(action, parameters)

    def populate(self):
        if not self.ready:
            return
        previous = self.selected_path
        table = self.query_one(DataTable)
        table.clear()
        query = self.query_one(Input).value.casefold()
        self.row_names = []
        for path, record in self.records.items():
            content = record["change"] in ("added", "removed") or "content" in record["fields"]
            metadata = any(f != "content" for f in record["fields"])
            elf = bool((record["old"] or {}).get("elf") or (record["new"] or {}).get("elf"))
            if query not in path.casefold() or not (
                self.category == "all"
                or self.category == "content"
                and content
                or self.category == "metadata"
                and metadata
                or self.category == "elf"
                and elf
            ):
                continue
            self.row_names.append(path)
            mark = {"added": "+", "removed": "-", "modified": "~", "unchanged": " "}[record["change"]]
            table.add_row(
                path,
                mark + " " + (", ".join(record["fields"]) or record["change"]) + (" · ELF" if elf else ""),
                key=path,
            )
        if self.row_names:
            row = self.row_names.index(previous) if previous in self.row_names else 0
            table.move_cursor(row=row)
            self.choose(self.row_names[row])
        else:
            self.choose(None)

    @on(DataTable.RowHighlighted, "#artifact-files")
    def highlighted(self, event):
        if event.row_key.value in self.row_names:
            self.choose(event.row_key.value)

    def choose(self, path):
        self.selected_path = path
        self.query_one("#artifact-selection", Static).update(path or "Artifact comparison / no matching files")
        self.update_sections()
        self.render_detail()
        self.refresh_bindings()

    def update_sections(self, selected="overview"):
        options = [("Overview", "overview")]
        if self.selected_path is not None:
            options += [("Text diff", "text"), ("Metadata / hashes", "metadata")]
            for tool in ("abidiff", "diffoscope", "bloaty"):
                if (tool, self.selected_path) in self.session.analyses:
                    options.append((tool + " report", tool))
        if ("diffoscope", None) in self.session.analyses:
            options.append(("Whole artifact · diffoscope", "whole"))
        options.append(("Context", "context"))
        select = self.query_one(Select)
        select.set_options(options)
        select.value = selected

    def overview(self, record):
        before, after = record["old"] or {}, record["new"] or {}
        lines = [record["change"].capitalize() + ": " + (", ".join(record["fields"]) or record["path"])]
        lines.append(f"Type: {before.get('type','absent')} → {after.get('type','absent')}")
        if "mode" in before or "mode" in after:
            modes = [f"{v['mode']:04o}" if "mode" in v else "absent" for v in (before, after)]
            lines.append("Mode: " + " → ".join(modes))
        if "size" in before or "size" in after:
            lines.append(f"Size: {before.get('size','absent')} → {after.get('size','absent')} bytes")
        if "target" in before or "target" in after:
            lines.append(
                "Target: "
                + json.dumps(before.get("target"), ensure_ascii=False)
                + " → "
                + json.dumps(after.get("target"), ensure_ascii=False)
            )
        lines.append("")
        if self.session.pair(record["path"], elf=True):
            lines.append("ELF pair · a: abidiff; b: Bloaty size profile. No ABI conclusion until analysis completes.")
        if self.session.pair(record["path"]):
            lines.append("Press d for diffoscope file detail; Shift+D compares the whole artifacts.")
        for tool in ("abidiff", "diffoscope", "bloaty"):
            result = self.session.analyses.get((tool, record["path"]))
            if result:
                lines.append(tool + ": " + result["status"] + " · f opens the report")
        return "\n".join(lines)

    def write_analysis(self, log, result):
        log.write(result["tool"] + ": " + result["status"] + "\n")
        log.write(result["note"] + "\n")
        if result.get("html_report"):
            log.write("HTML report: " + result["html_report"] + "\n")
        log.write(result["stdout"] or "(no standard output)")
        if result["stderr"]:
            log.write("\nDiagnostics:\n" + result["stderr"])
        if result["truncated"]:
            log.write("\n[Captured output truncated at configured report limit.]")

    def render_detail(self):
        self.generation += 1
        generation = self.generation
        log = self.query_one(RichLog)
        log.clear()
        section = self.query_one(Select).value
        record = self.records.get(self.selected_path)
        if section in ("abidiff", "diffoscope", "bloaty", "whole"):
            key = ("diffoscope", None) if section == "whole" else (section, self.selected_path)
            result = self.session.analyses.get(key)
            if result:
                self.write_analysis(log, result)
        elif section == "context":
            report = self.session.report()
            log.write(
                json.dumps(
                    {k: report[k] for k in ("inputs", "summary", "notes")},
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
            )
        elif record is None:
            log.write("No matching file changes.\n")
            for result in self.session.analyses.values():
                log.write(result["tool"] + ": " + result["status"] + " · f opens the report")
            log.write("\nTimestamps and extended attributes are not compared. No artifact programs are executed.")
        elif section == "metadata":
            log.write(
                json.dumps({"old": record["old"], "new": record["new"]}, ensure_ascii=False, sort_keys=True, indent=2)
            )
        else:
            if section == "overview":
                log.write(self.overview(record))
            entries = [entry for entry in (record["old"], record["new"]) if entry is not None]
            can_preview = all(e["type"] in FILE_KINDS for e in entries) and not any(e.get("elf") for e in entries)
            if section == "text" or can_preview:
                path = record["path"]

                async def preview():
                    async with self.io:
                        if self.closed or generation != self.generation:
                            return
                        try:
                            text = await asyncio.to_thread(self.session.text_diff, path)
                        except Exception as error:
                            text = "Unable to preview: " + str(error)
                    if not self.closed and generation == self.generation:
                        lines = text.splitlines()
                        content = "\n".join(lines if section == "text" else lines[:40])
                        log.write("\n")
                        log.write(diff_text(content))
                        if section != "text" and len(lines) > 40:
                            log.write("… f → Text diff opens the full bounded preview.")
                        log.scroll_home(animate=False)

                self.run_worker(preview())
        log.scroll_home(animate=False)

    async def analyze(self, tool, path):
        if self.busy or not self.ready:
            return
        generation = self.generation
        self.busy = True
        self.refresh_bindings()
        self.status("Running " + tool + ": " + (path or "whole artifacts") + " · q / Escape cancels")
        try:
            async with self.io:
                if self.closed:
                    return
                result = await asyncio.to_thread(self.session.analyze, tool, path)
            if self.closed:
                return
            if generation == self.generation:
                self.update_sections(tool if path is not None else "whole")
                self.render_detail()
            self.status(tool + ": " + result["status"] + " · " + (path or "whole artifacts"))
        except Cancelled:
            pass
        except Exception as error:
            if not self.closed:
                self.status("Unable to analyze: " + str(error))
        finally:
            self.busy = False
            if not self.closed:
                self.refresh_bindings()

    def action_abidiff(self):
        self.run_worker(self.analyze("abidiff", self.selected_path))

    def action_bloaty(self):
        self.run_worker(self.analyze("bloaty", self.selected_path))

    def action_diffoscope(self):
        self.run_worker(self.analyze("diffoscope", self.selected_path))

    def action_diffoscope_all(self):
        self.run_worker(self.analyze("diffoscope", None))

    @on(Input.Changed, "#artifact-search")
    def search_changed(self, event):
        self.populate()

    @on(Tabs.TabActivated, "#artifact-categories")
    def tab_changed(self, event):
        if event.tab.id == self.query_one(Tabs).active:
            self.category = event.tab.id.removeprefix("af-")
            self.populate()

    @on(Select.Changed, "#artifact-section")
    def section_changed(self, event):
        if event.value is not Select.BLANK and event.value == self.query_one(Select).value:
            self.render_detail()

    def action_search(self):
        self.query_one(Input).focus()

    def action_sections(self):
        self.query_one(Select).focus()
        self.query_one(Select).action_show_overlay()

    def action_down(self):
        if isinstance(self.focused, RichLog):
            self.focused.scroll_down()
        else:
            self.query_one(DataTable).action_cursor_down()

    def action_up(self):
        if isinstance(self.focused, RichLog):
            self.focused.scroll_up()
        else:
            self.query_one(DataTable).action_cursor_up()

    def action_back(self):
        if isinstance(self.focused, Input):
            self.query_one(DataTable).focus()
        else:
            self.action_quit()

    def action_quit(self):
        self.closed = True
        self.session.close()
        self.exit(130 if not self.ready or self.busy else 0)

    def on_unmount(self):
        self.closed = True
        self.session.close()
