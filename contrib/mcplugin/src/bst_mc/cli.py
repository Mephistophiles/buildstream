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
"""Create a project bookmark or open a temporary one in MC."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from . import __version__
from .integration import check_helper, check_project, install_helper


def project_arguments(directory, targets):
    targets = list(targets)
    if directory is None and targets and Path(targets[0]).is_dir():
        directory = targets.pop(0)
    path = Path(directory or ".").resolve()
    if not path.is_dir():
        raise ValueError(f"Project directory does not exist: {path}")
    if not any((parent / "project.conf").is_file() for parent in (path, *path.parents)):
        raise ValueError(f"No project.conf found at or above {path}; use bst-mc /path/to/project element.bst")
    return path, targets


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Examples: bst-mc /path/to/project app.bst; bst-mc -C /path/to/project app.bst",
    )
    parser.add_argument("--version", action="version", version=f"bst-mc {__version__}")
    parser.add_argument("--install-mc", action="store_true", help="Register the extfs helper for your MC user")
    parser.add_argument("-C", "--directory", help="BuildStream project directory")
    parser.add_argument("--option", nargs=2, action="append", default=[], metavar=("NAME", "VALUE"))
    parser.add_argument("-o", "--output", type=Path, help="Write a .bstmc bookmark instead of starting MC")
    parser.add_argument("targets", nargs="*", metavar="PROJECT_OR_ELEMENT",
                        help="Optional project directory, then elements (defaults to the project's default targets)")
    args = parser.parse_intermixed_args(argv)
    if args.install_mc and (args.directory or args.targets or args.option or args.output):
        parser.error("--install-mc cannot be combined with project arguments")
    try:
        if args.install_mc:
            path = install_helper()
            print(f"Installed MC helper: {path}\nRestart any running MC sessions.")
            return 0
        directory, targets = project_arguments(args.directory, args.targets)
        descriptor = {
            "format_version": 1,
            "directory": str(directory),
            "targets": targets,
            "options": args.option,
        }
        content = json.dumps(descriptor, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            # Bookmarks are user files; do not silently replace an existing one.
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(content)
            print(args.output.resolve())
            return 0
        helper = check_helper()
        with tempfile.TemporaryDirectory(prefix="bst-mc-") as temporary:
            bookmark = Path(temporary) / "project.bstmc"
            bookmark.write_text(content, encoding="utf-8")
            check_project(helper, bookmark)
            return subprocess.call(["mc", str(bookmark) + "/bstmc://"])
    except (OSError, ValueError) as error:
        print(f"bst-mc: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
