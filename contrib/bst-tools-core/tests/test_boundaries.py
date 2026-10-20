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
"""Architectural boundaries: shared code never depends on an application."""

import ast
from pathlib import Path


def test_application_packages_do_not_import_each_other():
    contrib = Path(__file__).resolve().parents[2]
    packages = {"bst_tree", "bst_diff", "bst_elf", "bst_blame", "bst_grep", "bst_cas", "bst_tools_core"}
    for package in packages:
        directory = contrib / package.replace("_", "-") / "src" / package
        for path in directory.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                imports = []
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imports = [node.module]
                for name in imports:
                    imported = name.split(".")[0]
                    shared = {"bst_tools_core"}
                    if package != "bst_tools_core":
                        shared.add("bst_cas")  # Optional shared index/library, also exposing a CLI.
                    assert imported not in packages - {package, *shared}, (path, name)


def test_removed_implementation_copies_are_not_shims():
    contrib = Path(__file__).resolve().parents[2]
    for application in ("bst-elf", "bst-blame", "bst-grep"):
        source = contrib / application / "src" / application.replace("-", "_")
        assert not (source / "project.py").exists()
        assert not (source / "process.py").exists()
    source = contrib / "bst-tree" / "src" / "bst_tree"
    for name in ("artifacts.py", "artifact_cli.py", "artifact_tui.py", "artifact_process.py"):
        assert not (source / name).exists()
