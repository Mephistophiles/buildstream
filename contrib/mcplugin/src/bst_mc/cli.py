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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-C", "--directory", default=".", help="BuildStream project directory")
    parser.add_argument("--option", nargs=2, action="append", default=[], metavar=("NAME", "VALUE"))
    parser.add_argument("-o", "--output", type=Path, help="Write a .bstmc bookmark instead of starting MC")
    parser.add_argument("targets", nargs="*", help="Elements (defaults to the project's default targets)")
    args = parser.parse_args(argv)
    descriptor = {
        "format_version": 1,
        "directory": str(Path(args.directory).resolve()),
        "targets": args.targets,
        "options": args.option,
    }
    try:
        if not Path(descriptor["directory"]).is_dir():
            raise ValueError("Project directory does not exist")
        content = json.dumps(descriptor, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            # Bookmarks are user files; do not silently replace an existing one.
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(content)
            print(args.output.resolve())
            return 0
        with tempfile.TemporaryDirectory(prefix="bst-mc-") as temporary:
            bookmark = Path(temporary) / "project.bstmc"
            bookmark.write_text(content, encoding="utf-8")
            return subprocess.call(["mc", str(bookmark) + "/bstmc://"])
    except (OSError, ValueError) as error:
        print(f"bst-mc: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
