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
import json
import subprocess
import sys

import pytest

from bst_blame import cli
from bst_tools_core.project import Graph
from helpers import listing, node


@pytest.fixture
def fake_project(monkeypatch):
    class FakeProject:
        missing = False

        def __init__(self, directory, options):
            self.directory, self.options = directory, options

        def graph(self, targets, deps):
            nodes = {"app.bst": node(kind="stack", run=["a.bst", "b.bst"]), "a.bst": node(), "b.bst": node()}
            if self.missing:
                nodes["missing.bst"] = node("buildable")
                nodes["app.bst"]["runtime-deps"].append("missing.bst")
            return Graph(nodes, ["app.bst"], set(nodes), deps)

        def listings(self, names):
            entries = {name: [("usr", "dir", 0, None), ("usr/lib/a", "reg", 4, None)] for name in names}
            return listing(names, entries), listing(names, entries, long=True)

        def commands(self, name):
            return {"config": ["bst", "show", "--", name]}

    monkeypatch.setattr(cli, "Project", FakeProject)
    monkeypatch.setattr(cli.shutil, "which", lambda name: name)
    return FakeProject


def json_rows(capsys):
    captured = capsys.readouterr()
    assert captured.err == ""
    return [json.loads(row) for row in captured.out.splitlines()]


def test_json_owners_paths_commands_and_complete_coverage(fake_project, capsys):
    assert cli.main(["--json", "--commands", "/usr/lib/a", "app.bst"]) == 0
    rows = json_rows(capsys)
    assert rows[0]["format_version"] == 1
    assert rows[0]["relationship"] == "potential-overlap"
    assert rows[0]["content_comparison"] == "not-compared"
    assert rows[0]["owners"][0]["dependency_paths"] == [["app.bst", "a.bst"]]
    assert rows[0]["owners"][0]["commands"]["config"] == ["bst", "show", "--", "a.bst"]
    assert rows[-1]["coverage"]["complete"] is True
    assert rows[-1]["coverage"]["inspected"] == 2


def test_incomplete_search_never_exits_success_even_with_owners(fake_project, capsys):
    fake_project.missing = True
    assert cli.main(["--json", "/usr/lib/a", "app.bst"]) == 2
    rows = json_rows(capsys)
    assert rows[0]["type"] == "path"
    assert rows[-1]["coverage"]["complete"] is False
    assert rows[-1]["coverage"]["missing"] == [{"element": "missing.bst", "state": "buildable"}]


def test_duplicates_omit_shared_directories_and_glob(fake_project, capsys):
    assert cli.main(["--json", "--duplicates", "--glob", "/**", "app.bst"]) == 0
    rows = json_rows(capsys)
    assert [r["path"] for r in rows if r["type"] == "path"] == ["/usr/lib/a"]


def test_no_match_distinguishes_complete_and_incomplete(fake_project, capsys):
    assert cli.main(["/missing", "app.bst"]) == 1
    captured = capsys.readouterr()
    assert "No matching paths in inspected local artifacts" in captured.out
    assert "complete" in captured.err
    fake_project.missing = True
    assert cli.main(["/missing", "app.bst"]) == 2
    assert "INCOMPLETE" in capsys.readouterr().err


def test_diff_implies_content_comparison(fake_project, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "compare", lambda project, index, coverage, *, text: calls.append(text))
    assert cli.main(["--json", "--diff", "/usr/lib/a", "app.bst"]) == 0
    assert calls == [True]
    assert json_rows(capsys)[0]["content_comparison"] == "incomplete"


def test_bad_query_fails_before_loading_project(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Project", lambda *args: pytest.fail("must reject before project loading"))
    assert cli.main(["--json", "../outside", "app.bst"]) == 2
    assert json_rows(capsys)[0]["type"] == "error"


def test_missing_bst_and_cancellation(monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli.main(["--json", "/a", "app.bst"]) == 2
    assert "missing from PATH" in json_rows(capsys)[0]["message"]
    monkeypatch.setattr(cli.shutil, "which", lambda name: name)

    def interrupt(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "Project", interrupt)
    assert cli.main(["--json", "/a", "app.bst"]) == 130
    assert "incomplete" in json_rows(capsys)[0]["message"]


def test_help_is_independent_of_other_tools():
    script = (
        "import sys; from bst_blame.cli import main\n"
        "try: main(['--help'])\nexcept SystemExit as error: assert error.code == 0\n"
        "assert not any(n.split('.')[0] in ('buildstream', 'bst_tree', 'bst_grep', 'textual') for n in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--compare-content" in result.stdout


def test_terminal_control_characters_are_escaped():
    assert cli.safe("path\n\x1b[31m") == "path\\x0a\\x1b[31m"
