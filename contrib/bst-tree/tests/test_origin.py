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
import importlib.util
import json
from pathlib import Path
import pytest
from bst_tree.cli import main
from bst_tree.origin import select, validate_report


def report():
    def entry(path, raw, value, refs=()):
        return {
            "path": path,
            "raw": raw,
            "value": value,
            "value_available": True,
            "origin": {"file": "config.yml", "line": 2, "column": 3, "synthetic": False},
            "references": [{"variable": name, "path": "/vars/" + name} for name in refs],
        }

    return {
        "format_version": 1,
        "query": "input-provenance",
        "elements": [
            {
                "element": "app.bst",
                "notes": [],
                "entries": [
                    entry("/env/CFLAGS", "%{flags}", "-O3 -g", ["flags"]),
                    entry("/vars/flags", "%{optimization} -g", "-O3 -g", ["optimization"]),
                    entry("/vars/optimization", "-O3", "-O3"),
                    entry("/config/build-commands/0", "echo %{flags}", "echo -O3 -g", ["flags"]),
                ],
            }
        ],
    }


def test_origin_follows_refs_and_preserves_selected_paths(tmp_path, capsys):
    data = report()
    path = tmp_path / "origins.json"
    path.write_text(json.dumps(data))
    assert main(["origin", "--from-report", str(path), "--path", "/env/CFLAGS", "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)["elements"][0]["entries"]
    assert [e["path"] for e in result] == ["/env/CFLAGS", "/vars/flags", "/vars/optimization"]
    assert [e["selected"] for e in result] == [True, False, False]
    assert main(["origin", "app.bst", "--from-report", str(path), "--path", "/env", "--no-follow"]) == 0
    text = capsys.readouterr().out
    assert "/env/CFLAGS" in text and "config.yml:2:3" in text and "/vars/optimization" not in text
    assert main(["origin", "--from-report", str(path), "--path", "/unknown"]) == 2
    assert not capsys.readouterr().out


def test_report_validation_and_missing_reference():
    data = report()
    data["elements"][0]["entries"].pop(1)
    selected = select(validate_report(data), paths=["/env"])
    assert selected["elements"][0]["unavailable_references"] == [{"variable": "flags", "path": "/vars/flags"}]
    for path in ["env/CFLAGS", "/env/~bad"]:
        with pytest.raises(ValueError):
            select(data, paths=[path])
    with pytest.raises(ValueError):
        validate_report({"format_version": 2})
    with pytest.raises(ValueError):
        select(data, element="missing")


def test_backend_requires_fork_and_does_not_guess(monkeypatch, capsys):
    from bst_tree.artifact_process import Execution

    monkeypatch.setattr(
        "bst_tree.headless.CommandRunner.__call__",
        lambda *args, **kwargs: Execution(2, "", "No such option: --provenance"),
    )
    assert main(["origin", "app.bst", "--path", "/env/CFLAGS", "--format", "json"]) == 2
    result = capsys.readouterr()
    assert not result.out and "requires this fork" in result.err


def test_native_serializer_preserves_real_and_synthetic_locations(tmp_path):
    pytest.importorskip("buildstream")
    from buildstream import _yaml
    from buildstream.node import Node

    core = Path(__file__).resolve().parents[3] / "src/buildstream/_input_provenance.py"
    spec = importlib.util.spec_from_file_location("buildstream._input_provenance", core)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "input.yml"
    path.write_text('flags: "%{optimization} -g"\noptimization: -O3\n')
    node = _yaml.load(str(path), path.name)
    result = module.build_report(
        "app",
        {"vars": node, "env": Node.from_dict({"CFLAGS": "%{flags}"})},
        {"vars": {"flags": "-O3 -g", "optimization": "-O3"}, "env": {"CFLAGS": "-O3 -g"}},
    )
    entries = {e["path"]: e for e in result["entries"]}
    assert entries["/vars/flags"]["raw"] == "%{optimization} -g"
    assert entries["/vars/flags"]["value"] == "-O3 -g"
    assert entries["/vars/flags"]["origin"]["line"] == 1
    assert entries["/vars/flags"]["origin"]["file"] == str(path)
    assert entries["/env/CFLAGS"]["origin"]["synthetic"] and entries["/env/CFLAGS"]["origin"]["file"] is None
