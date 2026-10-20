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
import os
import shutil
import subprocess
import tarfile

import pytest

from bst_tree.artifacts import ArtifactComparison

pytestmark = pytest.mark.skipif(
    os.environ.get("BST_TREE_TEST_TOOLS") != "1",
    reason="Set BST_TREE_TEST_TOOLS=1 with abidiff, diffoscope and gcc installed",
)


def test_real_artifact_analyzers(tmp_path):
    # Opting in must fail if an analyzer disappears from CI, rather than skip.
    for tool in ("gcc", "abidiff", "diffoscope", "bloaty"):
        assert shutil.which(tool), f"Required integration-test executable missing: {tool}"
    archives = []
    for side, symbol in (("old", "removed_function"), ("new", "added_function")):
        directory = tmp_path / side
        directory.mkdir()
        source = directory / "library.c"
        source.write_text(f"int {symbol}(void) {{ return 42; }}\n")
        library = directory / "library.so"
        subprocess.run(
            ["gcc", "-g", "-fPIC", "-shared", "-Wl,-soname,library.so", "-o", str(library), str(source)], check=True
        )
        (directory / "config").write_text(side + "\n")
        archive = tmp_path / (side + ".tar")
        with tarfile.open(archive, "w") as bundle:
            bundle.add(library, arcname="usr/lib/library.so")
            bundle.add(directory / "config", arcname="etc/config")
        archives.append(str(archive))
    session = ArtifactComparison(*archives, archives=True)
    try:
        session.load()
        abi = session.analyze("abidiff", "usr/lib/library.so")
        assert abi["status"] == "incompatible", abi
        assert abi["exit_code"] == 12
        assert "removed_function" in abi["stdout"]
        text = session.analyze("diffoscope", "etc/config", html=tmp_path / "report.html")
        assert text["status"] == "changed", text
        assert text["exit_code"] == 1
        assert "old" in text["stdout"] and "new" in text["stdout"]
        assert "<html" in (tmp_path / "report.html").read_text().lower()
        size = session.analyze("bloaty", "usr/lib/library.so")
        assert size["status"] == "analyzed", size
        assert "FILE SIZE" in size["stdout"] and "TOTAL" in size["stdout"]
        whole = session.analyze("diffoscope")
        assert whole["status"] == "changed", whole
        assert "config" in whole["stdout"]
    finally:
        session.close()


def test_real_identical_archives_and_empty_html(tmp_path):
    assert shutil.which("diffoscope")
    archive = tmp_path / "empty.tar"
    with tarfile.open(archive, "w"):
        pass
    session = ArtifactComparison(str(archive), str(archive), archives=True)
    try:
        session.load()
        result = session.analyze("diffoscope", html=tmp_path / "same.html")
        assert result["status"] == "no_reported_change", result
        assert (tmp_path / "same.html").is_file()
    finally:
        session.close()
