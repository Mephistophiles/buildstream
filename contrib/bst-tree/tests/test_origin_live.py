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
import os
import subprocess
import pytest
from bst_tree.cli import main

pytestmark = pytest.mark.skipif(
    os.environ.get("BST_TREE_TEST_ORIGIN") != "1", reason="Requires fork bst show --provenance backend"
)


def test_native_provenance_includes_conditions_and_transitive_variables(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "project"
    (root / "elements").mkdir(parents=True)
    (root / "project.conf").write_text(
        "name: origin-test\nmin-version: '2.0'\nelement-path: elements\noptions:\n  debug:\n    type: bool\n    description: Debug flags\n    default: false\nvariables:\n  optimization: -O2\nelements:\n  manual:\n    variables:\n      optimization: -O2.5\nsandbox:\n  build-os: linux\n  build-arch: aarch64\n"
    )
    (root / "flags.yml").write_text(
        'variables:\n  optimization: -O1\n  flags: "%{optimization} -g"\nenvironment:\n  CFLAGS: "%{flags}"\n'
    )
    (root / "elements/app.bst").write_text(
        "kind: manual\n(@): flags.yml\nvariables:\n  optimization: -O3\nconfig:\n  build-commands:\n  - echo %{flags}\n(?):\n- debug:\n    variables:\n      optimization: -O0\n"
    )
    for extra, expected in [([], "-O3"), (["--option", "debug", "True"], "-O0")]:
        assert main(["origin", "app.bst", "-C", str(root), "--path", "/env/CFLAGS", "--format", "json", *extra]) == 0
        entries = {e["path"]: e for e in json.loads(capsys.readouterr().out)["elements"][0]["entries"]}
        assert entries["/env/CFLAGS"]["value"] == expected + " -g"
        assert entries["/env/CFLAGS"]["origin"]["file"].endswith("flags.yml")
        assert entries["/vars/flags"]["raw"] == "%{optimization} -g"
        assert entries["/vars/optimization"]["value"] == expected
        assert entries["/vars/optimization"]["origin"]["file"].endswith("app.bst")
        assert entries["/vars/optimization"]["origin"]["line"] > 0
    for extra, expected in [
        ([], ["-O2", "-O2.5", "-O1", "-O3"]),
        (["--option", "debug", "True"], ["-O2", "-O2.5", "-O1", "-O3", "-O0"]),
    ]:
        assert (
            main(
                [
                    "origin",
                    "app.bst",
                    "-C",
                    str(root),
                    "--history",
                    "--path",
                    "/env/CFLAGS",
                    "--format",
                    "json",
                    *extra,
                ]
            )
            == 0
        )
        entries = {entry["path"]: entry for entry in json.loads(capsys.readouterr().out)["elements"][0]["entries"]}
        steps = entries["/vars/optimization"]["history"]
        assert [step["raw"] for step in steps] == expected
        assert steps[-1]["state"] == "effective"
        assert any(step["origin"]["file"].endswith("flags.yml") for step in steps)
        if extra:
            assert steps[-1]["conditions"][0]["expression"] == "debug"
        else:
            assert not any(step.get("conditions") for step in steps)
    assert (
        main(
            ["origin", "app.bst", "-C", str(root), "--history", "--path", "/config/build-commands", "--format", "json"]
        )
        == 0
    )
    entries = {entry["path"]: entry for entry in json.loads(capsys.readouterr().out)["elements"][0]["entries"]}
    assert entries["/config/build-commands"]["raw"] == ["echo %{flags}"]
    assert entries["/config/build-commands"]["value"] == ["echo -O3 -g"]
    assert entries["/config/build-commands"]["history"][-1]["state"] == "effective"
    conflict = subprocess.run(
        ["bst", "-C", str(root), "show", "--provenance", "--format", "%{name}", "app.bst"],
        capture_output=True,
        text=True,
    )
    assert conflict.returncode == 2 and "cannot be combined" in conflict.stderr
    plain = subprocess.run(
        ["bst", "-C", str(root), "show", "--deps", "none", "--format", "%{config}", "app.bst"],
        capture_output=True,
        text=True,
    )
    assert plain.returncode == 0 and "echo -O3 -g" in plain.stdout
