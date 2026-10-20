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
from bst_elf.process import CommandRunner


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


def test_export_runner_writes_binary_file_and_enforces_timeout(tmp_path):
    import sys

    runner = CommandRunner()
    try:
        path = tmp_path / "data"
        with path.open("wb") as output:
            result = runner(
                [
                    sys.executable,
                    "-c",
                    "import sys;sys.stdout.buffer.write(b'\\x00ELF');sys.stderr.write('diagnostic')",
                ],
                stdout_file=output,
            )
        assert result.returncode == 0 and path.read_bytes() == b"\x00ELF" and result.stderr == "diagnostic"
        assert runner([sys.executable, "-c", "import time;time.sleep(10)"], timeout=0.05).timed_out
    finally:
        runner.cancel()


def test_invalid_inputs_are_json_errors(tmp_path, capsys):
    assert main(["--json"]) == 2
    assert json.loads(capsys.readouterr().out)["complete"] is False
    assert main(["--archive", "x", "unused", "app.bst", "--json"]) == 2
    assert "cannot be combined" in json.loads(capsys.readouterr().out)["error"]
