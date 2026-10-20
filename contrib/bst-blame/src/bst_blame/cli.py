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
"""Find owning artifacts, report coverage, and optionally compare overlapping files."""

import argparse
import json
import os
import shlex
import shutil
import sys

from . import __version__
from .content import compare, describe
from .index import collect, normalize_path
from bst_tools_core.project import Project


def parser():
    result = argparse.ArgumentParser(
        prog="bst-blame",
        description="Find which local BuildStream artifacts contain a path, with dependency paths to their owners.",
        epilog="Example: bst-blame /usr/lib/libc++.so product.bst. "
        "Exit status: 0 found, 1 no matches, 2 incomplete/error, 130 interrupted. "
        "Default inspection does not pull artifacts or run builds. "
        "Optional content comparison exports artifacts and may retrieve missing data.",
    )
    result.add_argument("--version", action="version", version=f"bst-blame {__version__}")
    result.add_argument("-C", "--directory", help="BuildStream project directory (default: current directory)")
    result.add_argument(
        "--option",
        nargs=2,
        action="append",
        default=[],
        metavar=("NAME", "VALUE"),
        help="Set a project option; repeat as needed (strict mode)",
    )
    result.add_argument(
        "--deps",
        choices=("none", "run", "build", "all"),
        default="all",
        help="Dependency selection (default: all; build excludes targets)",
    )
    result.add_argument("--glob", action="store_true", help="Interpret PATH as a case-sensitive shell-style pattern")
    result.add_argument(
        "--duplicates",
        action="store_true",
        help="Only show paths with multiple owners; shared directories alone are omitted",
    )
    result.add_argument(
        "--compare-content",
        action="store_true",
        help="Hash overlapping regular files via artifact checkout; may retrieve missing data",
    )
    result.add_argument(
        "--diff", action="store_true", help="Also show bounded UTF-8 text diffs; implies --compare-content"
    )
    result.add_argument(
        "--commands", action="store_true", help="Show commands to inspect each owner's config, sources or artifact"
    )
    result.add_argument(
        "--json", action="store_true", help="Emit versioned JSON Lines with ownership records and a coverage summary"
    )
    result.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    result.add_argument(
        "path", metavar="PATH", help="Artifact-root path, e.g. /usr/lib/libc++.so; leading / is optional"
    )
    result.add_argument("targets", nargs="+", metavar="TARGET", help="Elements, including junction-qualified names")
    result.add_argument(
        "--cas-directory",
        default=os.environ.get("BST_CAS_DIRECTORY"),
        help="Enable shared bst-cas index using this local BuildStream cas directory (or BST_CAS_DIRECTORY)",
    )
    result.add_argument("--cas-index", help="Shared SQLite index path (or BST_CAS_INDEX); requires CAS mode")
    return result


def safe(value):
    return "".join(
        char if char.isprintable() else (f"\\x{ord(char):02x}" if ord(char) < 256 else f"\\u{ord(char):04x}")
        for char in value
    )


class Output:
    def __init__(self, args):
        self.json = args.json
        self.color = not args.json and (
            args.color == "always" or (args.color == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ)
        )

    def emit(self, record):
        if self.json:
            print(json.dumps({"format_version": 1, **record}, ensure_ascii=True), flush=True)
            return
        if record["type"] == "error":
            print("bst-blame: " + safe(record["message"]), file=sys.stderr)
            return
        if record["type"] == "summary":
            coverage = record["coverage"]
            eligible = coverage["selected"] - len(coverage["skipped"])
            status = "complete" if coverage["complete"] else "INCOMPLETE"
            print(
                f"Coverage: {coverage['inspected']}/{eligible} local artifacts inspected; "
                f"{len(coverage['missing'])} missing, {len(coverage['errors'])} errors; {status}.",
                file=sys.stderr,
            )
            for missing in coverage["missing"][:10]:
                print(f"  Not inspected: {safe(missing['element'])} ({safe(missing['state'])})", file=sys.stderr)
            if len(coverage["missing"]) > 10:
                print("  Further missing artifacts are listed in --json output.", file=sys.stderr)
            for error in coverage["errors"]:
                print(f"  {safe(error['element'])}: {safe(error['message'])}", file=sys.stderr)
            if not record["matched_paths"]:
                print("No matching paths in inspected local artifacts.")
            return
        owners = record["owners"]
        title = safe(record["path"])
        if self.color:
            title = "\033[1;36m" + title + "\033[0m"
        heading = f"{title}: {len(owners)} owner" + ("s" if len(owners) != 1 else "")
        if record["relationship"] != "single-owner":
            heading += "; " + record["relationship"].replace("-", " ")
        comparison = record["content_comparison"]
        if comparison == "different-types":
            heading += "; different entry types"
        elif comparison != "not-applicable":
            label = "link targets" if all(owner["kind"] == "symlink" for owner in owners) else "content"
            heading += "; " + label + " " + comparison.replace("-", " ")
        print(heading)
        if not record["metadata_equal"]:
            print("  File types, sizes, executable flags or link targets differ.")
        for owner in owners:
            info = f"{owner['kind']}, {owner['size']} bytes, {owner['state']}"
            if owner["executable"]:
                info += ", executable"
            print(f"  {safe(owner['element'])} [{info}]")
            if owner["kind"] == "symlink":
                print("    -> " + safe(owner["link_target"]))
            if "sha256" in owner:
                print("    sha256: " + owner["sha256"])
            for path in owner["dependency_paths"]:
                print("    " + " -> ".join(safe(name) for name in path))
            for action, command in owner.get("commands", {}).items():
                print(f"    {action}: {safe(shlex.join(command))}")
        for diff in record["diffs"]:
            for line in diff["lines"]:
                rendered = safe(line.removesuffix("\n"))
                if self.color and line.startswith(("+", "-")):
                    rendered = ("\033[32m" if line.startswith("+") else "\033[31m") + rendered + "\033[0m"
                print(rendered)
            if diff["truncated"]:
                print("[Diff truncated at 200 lines]")
        print()


def _run(args, output, cas):
    pattern = normalize_path(args.path)
    if shutil.which("bst") is None:
        raise ValueError("bst is missing from PATH")
    project = Project(args.directory, args.option)
    project.cas_index = cas
    graph = project.graph(args.targets, args.deps)
    if sys.stderr.isatty():
        print("Inspecting locally cached artifacts...", file=sys.stderr)
    index, coverage = collect(project, graph, pattern, args.glob)
    if args.duplicates:
        index = {
            path: owners
            for path, owners in index.items()
            if len(owners) > 1 and any(owner["kind"] != "directory" for owner in owners)
        }
    comparing = args.compare_content or args.diff
    if comparing:
        if sys.stderr.isatty():
            print("Comparing artifact contents (checkout may retrieve missing data)...", file=sys.stderr)
        compare(project, index, coverage, text=args.diff)
    for path, owners in sorted(index.items()):
        if args.commands:
            for owner in owners:
                owner["commands"] = project.commands(owner["element"])
        output.emit(describe(path, owners, compared=comparing, text=args.diff))
    if cas is not None:
        coverage["cas_index"] = cas.stats()
    output.emit({"type": "summary", "matched_paths": len(index), "coverage": coverage})
    return 2 if not coverage["complete"] else (0 if index else 1)


def run(args, output):
    if args.cas_index and not args.cas_directory:
        raise ValueError("--cas-index requires --cas-directory or BST_CAS_DIRECTORY")
    if not args.cas_directory:
        return _run(args, output, None)
    try:
        from bst_cas.integration import open_index
    except ImportError as error:
        raise ValueError("CAS mode requires bst-cas; install ./contrib/bst-cas in the same environment") from error
    with open_index(args) as cas:
        return _run(args, output, cas)


def main(argv=None):
    args = parser().parse_args(argv)
    output = Output(args)
    try:
        return run(args, output)
    except BrokenPipeError:
        sys.stdout = open(os.devnull, "w")
        return 0
    except KeyboardInterrupt:
        output.emit({"type": "error", "message": "Inspection interrupted; results are incomplete"})
        return 130
    except (OSError, ValueError) as error:
        output.emit({"type": "error", "message": str(error)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
