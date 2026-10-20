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
"""CLI contracts: independent installation, provenance, streaming and partial failure."""

from contextlib import contextmanager
import json
import shutil
import subprocess
import sys

import pytest

from bst_grep import cli
from bst_grep.project import Paths


@pytest.fixture
def project(tmp_path, monkeypatch):
    class FakeProject:
        def __init__(self, directory, options):
            self.directory, self.options = directory, options

        def select(self, targets, deps, *, workspaces):
            assert targets == ["app.bst"]
            return {"app.bst": str(tmp_path), "sdk.bst:lib.bst": ""}

        def paths(self, targets, deps):
            return Paths({"app.bst": ["sdk.bst:lib.bst"], "sdk.bst:lib.bst": []}, ["app.bst"])

        @contextmanager
        def sources(self, name, workspace):
            root = tmp_path / ("app" if workspace else "lib")
            root.mkdir(exist_ok=True)
            (root / "main.c").write_text("before\nneedle\nafter\n")
            yield root, "workspace" if workspace else "checkout"

        @contextmanager
        def configuration(self, name):
            root = tmp_path / "config"
            root.mkdir(exist_ok=True)
            (root / "config.yaml").write_text("build-commands:\n- echo needle\n")
            yield root, "resolved"

    monkeypatch.setattr(cli, "Project", FakeProject)
    monkeypatch.setattr(cli.shutil, "which", lambda executable: executable)
    return FakeProject


@pytest.mark.skipif(shutil.which("rg") is None, reason="Install ripgrep")
def test_json_provenance_context_and_why(project, capsys):
    assert cli.main(["--json", "--why", "--context", "1", "needle", "app.bst"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert all(r["format_version"] == 1 for r in rows)
    matches = [r for r in rows if r["type"] == "match"]
    assert [r["origin"] for r in matches] == ["workspace", "checkout"]
    assert matches[1]["dependency_paths"] == [["app.bst", "sdk.bst:lib.bst"]]
    assert all(r["path"] == "main.c" for r in matches)
    assert rows[-1] == {
        "format_version": 1,
        "type": "summary",
        "complete": True,
        "selected": 2,
        "searched": 2,
        "failed": 0,
        "matched_lines": 2,
        "matched_files": 2,
    }


@pytest.mark.skipif(shutil.which("rg") is None, reason="Install ripgrep")
def test_config_and_no_matches(project, capsys):
    assert cli.main(["--in", "config", "--json", "echo", "app.bst"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["origin"] == "resolved"
    assert rows[0]["path"] == "config.yaml"
    assert cli.main(["absent", "app.bst"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "0 matching lines" in captured.err


@pytest.mark.skipif(shutil.which("rg") is None, reason="Install ripgrep")
def test_partial_failure_never_reports_success(project, monkeypatch, capsys):
    original = project.sources

    @contextmanager
    def sources(self, name, workspace):
        if name.startswith("sdk"):
            raise ValueError("sources unavailable")
        with original(self, name, workspace) as result:
            yield result

    monkeypatch.setattr(project, "sources", sources)
    assert cli.main(["--json", "needle", "app.bst"]) == 2
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["type"] == "match"
    assert rows[-2]["type"] == "error"
    assert rows[-2]["element"] == "sdk.bst:lib.bst"
    assert rows[-1]["complete"] is False
    assert rows[-1]["searched"] == 1
    assert rows[-1]["failed"] == 1


def test_missing_dependency_and_invalid_pattern_fail_before_project(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Project", lambda *a: pytest.fail("must fail before querying project"))
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli.main(["--json", "needle", "app.bst"]) == 2
    assert "missing from PATH" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("rg") is None, reason="Install ripgrep")
def test_invalid_pattern_does_not_checkout(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Project", lambda *a: pytest.fail("must validate regex before loading"))
    assert cli.main(["--json", "[", "app.bst"]) == 2
    assert json.loads(capsys.readouterr().out)["type"] == "error"


def test_interruption_is_incomplete(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "validate", lambda args: None)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(project, "select", interrupted)
    assert cli.main(["--json", "needle", "app.bst"]) == 130
    assert "incomplete" in json.loads(capsys.readouterr().out)["message"]


def test_terminal_text_escapes_control_characters(capsys):
    args = cli.parser().parse_args(["--color", "never", "needle", "app.bst"])
    output = cli.Output(args)
    output.emit(
        {
            "type": "match",
            "element": "app.bst",
            "path": "bad\nname.c",
            "line": 3,
            "column": 2,
            "text": "needle\x1b[31m",
        }
    )
    assert capsys.readouterr().out == "app.bst::bad\\x0aname.c:3:2:needle\\x1b[31m\n"


def test_help_does_not_import_buildstream_tree_or_textual():
    script = (
        "import sys; from bst_grep.cli import main; "
        "\ntry: main(['--help'])\n"
        "except SystemExit as error: assert error.code == 0\n"
        "assert not any(n.split('.')[0] in ('textual', 'bst_tree', 'buildstream') for n in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--deps" in result.stdout


def test_context_precedence_and_leading_dash():
    args = cli.parser().parse_args(["--context", "3", "-A", "0", "--", "-fsanitize", "app.bst"])
    assert args.pattern == "-fsanitize"
    assert args.after_context == 0
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["--context", "-1", "needle", "app.bst"])


def test_color_highlights_only_match_bytes():
    record = {"text": "ж needle tail", "submatches": [{"start": 3, "end": 9}]}
    assert cli.highlight(record) == "ж \033[1;31mneedle\033[0m tail"
