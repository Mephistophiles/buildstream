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
import io
import json
import tarfile

import pytest

from bst_tree.cli import main


def pair(tmp_path):
    result = []
    for side, text in [("old", b"old\n"), ("new", b"new\n")]:
        path = tmp_path / (side + ".tar")
        with tarfile.open(path, "w") as bundle:
            member = tarfile.TarInfo("etc/config")
            member.size = len(text)
            member.mode = 0o644
            bundle.addfile(member, io.BytesIO(text))
        result.append(str(path))
    return result


def test_artifact_cli_offline_diff_and_exit_codes(tmp_path, capsys):
    a, b = pair(tmp_path)
    assert main(["artifact-diff", a, b, "--archives", "--format", "json", "--check"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["comparison"] == "artifacts"
    assert report["summary"]["modified"] == 1
    assert main(["artifact-diff", a, a, "--archives", "--check"]) == 0
    capsys.readouterr()
    assert main(["artifact-diff", a, b, "--archives", "--path", "etc/config"]) == 0
    output = capsys.readouterr().out
    assert "-old" in output and "+new" in output
    assert main(["artifact-diff", a, b, "--archives", "--path", "absent"]) == 2
    assert "absent" in capsys.readouterr().err


def test_artifact_cli_analyzer_errors_are_not_differences(tmp_path, capsys, monkeypatch):
    a, b = pair(tmp_path)
    monkeypatch.setattr("bst_tree.artifacts.shutil.which", lambda name: None)
    assert main(["artifact-diff", a, b, "--archives", "--diffoscope", "--format", "json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["analyses"][0]["status"] == "unavailable"
    assert main(["artifact-diff", a, b, "--archives", "--diffoscope"]) == 2
    assert "incomplete" in capsys.readouterr().out


@pytest.mark.parametrize(
    "flags",
    [
        ["--archives", "-C", "/project"],
        ["--archives", "--option", "arch", "x86-64"],
        ["--archives", "--diffoscope-html", "report.html", "--path", "a", "--path", "b"],
        ["--archives", "--tui"],
    ],
)
def test_artifact_cli_invalid_modes_do_not_export(tmp_path, capsys, flags):
    a, b = pair(tmp_path)
    assert main(["artifact-diff", a, b, *flags]) == 2
    assert capsys.readouterr().err


@pytest.mark.parametrize(
    "flag,value", [("--timeout", "0"), ("--timeout", "nan"), ("--timeout", "inf"), ("--report-limit", "0")]
)
def test_artifact_cli_invalid_limits(flag, value):
    with pytest.raises(SystemExit) as error:
        main(["artifact-diff", "old", "new", flag, value])
    assert error.value.code == 2
