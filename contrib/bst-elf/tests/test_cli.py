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
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest
from bst_elf.archive import scan
from bst_elf.cli import main
from bst_elf.elf import read_elf
from bst_tools_core.process import CommandRunner


def build_archives(tmp_path):
    assert shutil.which("cc"), "C compiler required for ELF tests"
    source = tmp_path / "source.c"
    source.write_text("int answer(void) { return 42; }\n")
    library = tmp_path / "libanswer.so"
    subprocess.run(
        ["cc", "-shared", "-nostdlib", "-fPIC", "-Wl,-soname,libanswer.so", "-o", str(library), str(source)],
        check=True,
    )
    source.write_text("extern int answer(void); int entry(void) { return answer(); }\n")
    app = tmp_path / "app"
    subprocess.run(
        [
            "cc",
            "-shared",
            "-nostdlib",
            "-fPIC",
            str(source),
            "-L" + str(tmp_path),
            "-Wl,--no-as-needed",
            "-lanswer",
            "-Wl,-rpath,$ORIGIN/../lib",
            "-o",
            str(app),
        ],
        check=True,
    )
    archives = []
    for owner, file, arcname in [("app", app, "bin/app"), ("runtime", library, "lib/libanswer.so")]:
        archive = tmp_path / (owner + ".tar")
        with tarfile.open(archive, "w") as bundle:
            bundle.add(file, arcname=arcname)
        archives += ["--archive", owner, str(archive)]
    return archives


def test_real_elf_offline_cli_and_missing_provider(tmp_path, capsys):
    args = build_archives(tmp_path)
    assert main([*args, "--path", "/bin/app", "--json", "--check"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["coverage"]["complete"] and len(report["files"]) == 2
    app = next(f for f in report["files"] if f["owner"] == "app")
    assert app["elf"]["needed"] == ["libanswer.so"]
    assert app["elf"]["runpath"] == ["$ORIGIN/../lib"]
    assert app["elf"]["machine"].startswith("EM_") and app["sha256"]
    assert main([*args[:3], "--path", "/bin/app", "--json", "--check"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["findings"][0]["status"] == "missing"
    assert main([*args, "--path", "/bin/app", "--expect-machine", "EM_UNKNOWN", "--check"]) == 1
    assert "Unexpected architecture" in capsys.readouterr().out


def test_malformed_archive_and_elf_coverage(tmp_path, capsys):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as bundle:
        info = tarfile.TarInfo("bad")
        data = b"\x7fELFbroken"
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    assert main(["--archive", "bad", str(archive), "--json"]) == 2
    assert not json.loads(capsys.readouterr().out)["coverage"]["complete"]
    with tarfile.open(archive, "w") as bundle:
        bundle.addfile(tarfile.TarInfo("../escape"))
    with pytest.raises(ValueError):
        scan(archive, "bad")



def test_invalid_inputs_are_json_errors(tmp_path, capsys):
    assert main(["--json"]) == 2
    assert json.loads(capsys.readouterr().out)["complete"] is False
    assert main(["--archive", "x", "unused", "app.bst", "--json"]) == 2
    assert "cannot be combined" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize(
    'environment, expected',
    [('{}', []), ('LD_LIBRARY_PATH: ""', []),
     ('LD_LIBRARY_PATH: /opt/lib:/vendor/lib;/extra', ['/opt/lib', '/vendor/lib', '/extra']),
     ('LD_LIBRARY_PATH: ":/opt/lib:"', ['', '/opt/lib', ''])],
)
def test_project_library_path(environment, expected, monkeypatch):
    from bst_elf.cli import project_library_path
    from bst_tools_core.project import Project

    project = Project('/project', [('arch', 'arm64')])
    def report(targets, fields, deps):
        assert targets == ['product.bst']
        assert fields == ('name', 'env') and deps == 'none'
        return [{'name': 'product.bst', 'env': environment}]
    monkeypatch.setattr(project, 'report', report)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/host-only')
    assert project_library_path(project, ['product.bst']) == expected


@pytest.mark.parametrize('environment', ['[]', 'LD_LIBRARY_PATH: []', 'LD_LIBRARY_PATH: 42', '[broken'])
def test_invalid_project_environment(environment, monkeypatch):
    from bst_elf.cli import project_library_path
    from bst_tools_core.project import Project

    project = Project()
    monkeypatch.setattr(project, 'report', lambda *a: [{'name': 'app', 'env': environment}])
    with pytest.raises(ValueError):
        project_library_path(project, ['app'])


def test_conflicting_target_environments(monkeypatch):
    from bst_elf.cli import project_library_path
    from bst_tools_core.project import Project

    project = Project()
    monkeypatch.setattr(project, 'report', lambda *a: [
        {'name': 'a', 'env': 'LD_LIBRARY_PATH: /a'},
        {'name': 'b', 'env': 'LD_LIBRARY_PATH: /b'},
    ])
    with pytest.raises(ValueError, match='inspect them separately'):
        project_library_path(project, ['a', 'b'])


def test_live_config_library_path_resolves_real_elf(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    import bst_elf.cli as cli
    from bst_tools_core.project import Project

    build_archives(tmp_path)
    # Move the provider away from both RUNPATH and the default search paths.
    archive = tmp_path / 'live.tar'
    with tarfile.open(archive, 'w') as bundle:
        bundle.add(tmp_path / 'app', arcname='bin/app')
        bundle.add(tmp_path / 'libanswer.so', arcname='vendor/lib/libanswer.so')
    graph = SimpleNamespace(
        selected={'product.bst'},
        nodes={'product.bst': {'kind': 'import', 'state': 'cached', 'full-key': 'key'}},
        paths=lambda owner: [[owner]],
    )
    monkeypatch.setattr(Project, 'graph', lambda *a: graph)
    def report(self, targets, fields, deps='none'):
        if fields == ('name', 'env'):
            return [{'name': 'product.bst', 'env': 'LD_LIBRARY_PATH: /vendor/lib'}]
        return [{'full-key': 'key', 'artifact-cas-digest': 'digest'}]
    monkeypatch.setattr(Project, 'report', report)
    class Runner:
        def __call__(self, argv, **kwargs):
            kwargs['stdout_file'].write(archive.read_bytes())
            return SimpleNamespace(returncode=0, timed_out=False, truncated=False)
        def cancel(self):
            pass
    monkeypatch.setattr(cli, 'CommandRunner', Runner)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/host-only')
    assert main(['product.bst', '--path', '/bin/app', '--library-path', '/explicit', '--json', '--check']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['profile']['library_path'] == ['/explicit', '/vendor/lib']
    assert not result['findings'] and result['coverage']['complete']
    # Offline input must not query BuildStream or inherit host/project paths.
    assert main(['--archive', 'product', str(archive), '--path', '/bin/app', '--json', '--check']) == 1
    assert json.loads(capsys.readouterr().out)['profile']['library_path'] == []
