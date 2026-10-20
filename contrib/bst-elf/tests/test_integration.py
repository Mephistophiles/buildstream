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
from bst_elf.cli import main
from test_cli import build_archives

pytestmark = pytest.mark.skipif(
    os.environ.get("BST_ELF_TEST_LIVE") != "1", reason="Set BST_ELF_TEST_LIVE=1 with bst/buildbox"
)


def test_real_buildstream_artifact_owners(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    build_archives(tmp_path)
    project = tmp_path / "project"
    elements = project / "elements"
    elements.mkdir(parents=True)
    (project / "project.conf").write_text(
        "name: elf-test\nmin-version: '2.0'\nelement-path: elements\nsandbox:\n  build-os: linux\n  build-arch: aarch64\n"
    )
    for name, filename, target in [("app", "app", "bin/app"), ("runtime", "libanswer.so", "lib/libanswer.so")]:
        source = project / name / target
        source.parent.mkdir(parents=True)
        source.write_bytes((tmp_path / filename).read_bytes())
        (elements / (name + ".bst")).write_text("kind: import\nsources:\n- kind: local\n  path: " + name + "\n")
    (elements / "product.bst").write_text("kind: stack\ndepends:\n- app.bst\n- runtime.bst\n")
    subprocess.run(["bst", "-C", str(project), "build", "app.bst", "runtime.bst"], capture_output=True, check=True)
    assert main(["-C", str(project), "product.bst", "--path", "/bin/app", "--json", "--check"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["coverage"]["complete"] and report["coverage"]["inspected"] == 2
    assert {f["owner"] for f in report["files"]} == {"app.bst", "runtime.bst"}
    assert all(f["artifact_digest"] for f in report["files"])
