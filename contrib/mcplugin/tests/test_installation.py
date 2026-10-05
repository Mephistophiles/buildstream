# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Regressions for standalone installation and fail-fast MC launch."""

import json
import os
from pathlib import Path
import shlex
import subprocess
from types import SimpleNamespace

import pytest

from bst_mc import __version__, cli, integration


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    (root / "project.conf").write_text("name: test\n")
    return root


@pytest.mark.parametrize("form", ["positional", "directory", "cwd", "subdirectory"])
def test_project_argument_forms(project, tmp_path, monkeypatch, form):
    output = tmp_path / "project.bstmc"
    args = ["-o", str(output)]
    directory = project
    if form == "positional":
        args += [str(project), "app.bst", "sdk.bst:lib.bst"]
    elif form == "directory":
        args += ["-C", str(project), "app.bst", "sdk.bst:lib.bst"]
    else:
        if form == "subdirectory":
            directory = project / "elements"
            directory.mkdir()
        monkeypatch.chdir(directory)
        args += ["app.bst", "sdk.bst:lib.bst"]
    assert cli.main(args) == 0
    result = json.loads(output.read_text())
    assert result["directory"] == str(directory)
    assert result["targets"] == ["app.bst", "sdk.bst:lib.bst"]


def test_project_directory_without_targets(project, tmp_path):
    output = tmp_path / "project.bstmc"
    assert cli.main([str(project), "-o", str(output)]) == 0
    assert json.loads(output.read_text())["targets"] == []


def test_options_between_directory_and_targets(project, tmp_path):
    output = tmp_path / "project.bstmc"
    assert cli.main([str(project), "--option", "arch", "aarch64", "app.bst", "-o", str(output)]) == 0
    descriptor = json.loads(output.read_text())
    assert descriptor["directory"] == str(project)
    assert descriptor["options"] == [["arch", "aarch64"]]
    assert descriptor["targets"] == ["app.bst"]


def test_non_project_does_not_launch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.subprocess, "call", lambda *a: pytest.fail("MC must not start"))
    assert cli.main(["-C", str(tmp_path), "app.bst"]) == 1
    assert "No project.conf" in capsys.readouterr().err


def test_missing_helper_does_not_launch(project, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(integration, "helper_path", lambda: tmp_path / "missing-helper")
    monkeypatch.setattr(cli.subprocess, "call", lambda *a: pytest.fail("MC must not start"))
    assert cli.main([str(project), "app.bst"]) == 1
    assert "bst-mc --install-mc" in capsys.readouterr().err


def test_project_load_error_does_not_launch(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "check_helper", lambda: Path("/fake/bstmc"))
    bookmarks = []

    def run(args, **kwargs):
        assert args[:2] == ["/fake/bstmc", "list"]
        bookmarks.append(Path(args[2]))
        assert json.loads(bookmarks[-1].read_text())["targets"] == ["missing.bst"]
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(integration.subprocess, "run", run)
    monkeypatch.setattr(cli.subprocess, "call", lambda *a: pytest.fail("MC must not start"))
    assert cli.main([str(project), "missing.bst"]) == 1
    assert "MC was not started" in capsys.readouterr().err
    assert not bookmarks[0].exists()


def test_mc_reports_custom_user_directory(tmp_path, monkeypatch):
    expected = tmp_path / "custom profile" / "extfs.d"
    monkeypatch.setattr(integration.shutil, "which", lambda name: "/bin/" + name)

    def run(args, **kwargs):
        assert args == ["mc", "--datadir-info"]
        assert kwargs["env"]["LC_ALL"] == "C"
        return SimpleNamespace(returncode=0, stdout=(
            f"[System data]\n extfs.d: /usr/lib/mc/extfs.d/\n[User data]\n extfs.d: {expected}/\n"
        ))

    monkeypatch.setattr(integration.subprocess, "run", run)
    assert integration.helper_path() == expected / "bstmc"


def test_mc_without_extfs(tmp_path, monkeypatch):
    monkeypatch.setattr(integration.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(integration.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout="[User data]\nData directory: /tmp/mc\n"))
    with pytest.raises(ValueError, match="extfs"):
        integration.helper_path()


def test_install_helper_idempotent_and_executable(tmp_path, monkeypatch):
    helper = tmp_path / "user data" / "extfs.d" / "bstmc"
    monkeypatch.setattr(integration, "helper_path", lambda: helper)
    assert cli.main(["--install-mc"]) == 0
    assert os.access(helper, os.X_OK)
    before = helper.read_bytes()
    assert cli.main(["--install-mc"]) == 0
    assert before == helper.read_bytes()
    result = subprocess.run([str(helper), "--version"], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == f"bst-mc {__version__}"


def test_install_preserves_venv_interpreter_and_quotes_spaces(tmp_path, monkeypatch):
    helper = tmp_path / "extfs.d" / "bstmc"
    python = tmp_path / "pipx venv's" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(integration.sys.executable)
    monkeypatch.setattr(integration, "helper_path", lambda: helper)
    monkeypatch.setattr(integration.sys, "executable", str(python))
    integration.install_helper()
    command = shlex.split(helper.read_text().splitlines()[-1])
    assert command == ["exec", str(python), "-m", "bst_mc.vfs", "$@"]


def test_install_migrates_old_symlink_without_overwriting_target(tmp_path, monkeypatch):
    old = tmp_path / "old-bstmc"
    old.write_text("from bst_mc.vfs import main\n")
    helper = tmp_path / "bstmc"
    helper.symlink_to(old)
    monkeypatch.setattr(integration, "helper_path", lambda: helper)
    integration.install_helper()
    assert not helper.is_symlink()
    assert old.read_text() == "from bst_mc.vfs import main\n"


def test_install_refuses_unrelated_helper(tmp_path, monkeypatch):
    helper = tmp_path / "bstmc"
    helper.write_text("unrelated program\n")
    monkeypatch.setattr(integration, "helper_path", lambda: helper)
    with pytest.raises(ValueError, match="Refusing"):
        integration.install_helper()
    assert helper.read_text() == "unrelated program\n"


@pytest.mark.parametrize("version", ["bst-mc 0.1.0", "", "some other helper"])
def test_outdated_helper_is_reported(tmp_path, monkeypatch, version):
    helper = tmp_path / "bstmc"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)
    monkeypatch.setattr(integration, "helper_path", lambda: helper)
    monkeypatch.setattr(integration.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout=version, stderr=""))
    with pytest.raises(ValueError, match="bst-mc --install-mc"):
        integration.check_helper()


def test_helper_probe_detects_missing_bst(tmp_path, monkeypatch):
    helper = tmp_path / "bstmc"
    monkeypatch.setattr(integration, "helper_path", lambda: helper)
    integration.install_helper()
    monkeypatch.setattr(integration.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="bst is not on PATH"):
        integration.check_helper()
