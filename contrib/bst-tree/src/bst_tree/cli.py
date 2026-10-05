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
"""Command line entry point; snapshot and diff never import Textual."""

import argparse
import json
import os
import subprocess
import sys
import threading

from .adapter import capture
from .diff import DIFF_FIELDS, compare, has_changes, render_tree
from .model import read_snapshot, write_snapshot
from .inspection import ProjectInspector


def parser():
    result = argparse.ArgumentParser(
        prog="bst-tree",
        description="Explore BuildStream dependency graphs and compare portable JSON snapshots.",
        epilog="Run bst-tree COMMAND --help for examples and command options.",
    )
    commands = result.add_subparsers(dest="command", required=True)
    for command in ("browse", "snapshot"):
        description = (
            "Browse a live project or a saved snapshot in a terminal."
            if command == "browse" else "Save the full dependency graph as a portable JSON snapshot."
        )
        sub = commands.add_parser(
            command, help=description, description=description,
            epilog=(
                "Examples: bst-tree browse -C /path/to/project app.bst; "
                "bst-tree browse --snapshot graph.json. Snapshot mode has no live element inspection."
                if command == "browse" else
                "Example: bst-tree snapshot -C /path/to/project app.bst -o graph.json. "
                "Existing output files are replaced atomically."
            ),
        )
        sub.add_argument("targets", nargs="*" if command == "browse" else "+", metavar="TARGET",
                         help="Explicit element names, including junction-qualified names; required for live projects")
        sub.add_argument("-C", "--directory", help="Project directory (default: current directory)")
        sub.add_argument("--option", nargs=2, action="append", default=[], metavar=("NAME", "VALUE"),
                         help="Set a BuildStream project option; may be repeated (queries use strict mode)")
        if command == "browse":
            sub.add_argument("--snapshot", metavar="FILE",
                             help="Browse saved JSON without bst; cannot be combined with project arguments")
        else:
            sub.add_argument("-o", "--output", required=True, metavar="FILE",
                             help="Output JSON snapshot; replaces an existing file")
    diff = commands.add_parser(
        "diff", help="Compare two snapshots without loading a project",
        description="Compare saved graphs; no BuildStream installation or terminal is required.",
        epilog="Example: bst-tree diff before.json after.json --structure-only --check. "
               "Exit status: 0 success, 1 differences with --check, 2 error.",
    )
    diff.add_argument("old", help="Baseline JSON snapshot")
    diff.add_argument("new", help="Updated JSON snapshot")
    diff.add_argument("--format", choices=("tree", "json"), default="tree",
                      help="Output format (default: tree); JSON never includes color escapes")
    diff.add_argument("--all", action="store_true", help="Include unchanged branches")
    diff.add_argument("--reverse", action="store_true", help="Show dependencies followed by their consumers")
    diff.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Color tree output (default: auto; respects NO_COLOR)",
    )
    selection = diff.add_mutually_exclusive_group()
    selection.add_argument(
        "--fields",
        nargs="+",
        choices=DIFF_FIELDS,
        help="Only compare these fields; node, edge and target changes are always included",
    )
    selection.add_argument(
        "--ignore-fields",
        nargs="+",
        choices=DIFF_FIELDS,
        help="Compare all fields except these (e.g. key to hide cache-key changes)",
    )
    selection.add_argument(
        "--structure-only",
        action="store_true",
        help="Only compare nodes, dependencies, dependency types and targets",
    )
    diff.add_argument("--check", action="store_true", help="Exit 1 when the selected comparison has changes")
    return result


class CancellableRunner:
    """Own subprocess lifetime so leaving the TUI cancels an active bst call."""

    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.cancelled = False
        self.diagnostics = []

    def __call__(self, args, **kwargs):
        kwargs.pop("check", None)
        # Capture stderr in TUI mode so diagnostics don't corrupt the display.
        with self.lock:
            if self.cancelled:
                raise ValueError("Loading cancelled")
            process = self.process = subprocess.Popen(args, stderr=subprocess.PIPE, **kwargs)
        stdout, stderr = process.communicate()
        if process.returncode:
            raise ValueError(stderr.strip() or f"bst exited {process.returncode}")
        if stderr.strip():
            self.diagnostics.append(stderr.strip())
        return subprocess.CompletedProcess(args, process.returncode, stdout)

    def cancel(self):
        with self.lock:
            self.cancelled = True
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    try:
        if args.command == "diff":
            old, new = read_snapshot(args.old), read_snapshot(args.new)
            fields = set(DIFF_FIELDS)
            if args.structure_only:
                fields = set()
            elif args.fields is not None:
                fields = set(args.fields)
            elif args.ignore_fields:
                fields -= set(args.ignore_fields)
            delta = compare(old, new, fields=fields)
            print(
                json.dumps(delta, ensure_ascii=False, indent=2, sort_keys=True)
                if args.format == "json"
                else render_tree(
                    old,
                    new,
                    delta,
                    args.all,
                    args.color == "always"
                    or (args.color == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ),
                    reverse=args.reverse,
                )
            )
            return 1 if args.check and has_changes(delta) else 0
        if args.command == "snapshot":
            write_snapshot(capture(args.targets, args.directory, args.option), args.output)
            return 0
        if args.snapshot and (args.targets or args.directory or args.option):
            arguments.error("--snapshot cannot be combined with project arguments")
        if not args.snapshot and not args.targets:
            arguments.error("browse requires targets or --snapshot")
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError("browse requires a terminal (TTY); use snapshot or diff for noninteractive use")
        from .tui import Explorer

        runner = CancellableRunner()

        def inspector_factory():
            inspection_runner = CancellableRunner()
            return ProjectInspector(args.directory, args.option, run=inspection_runner, cancel=inspection_runner.cancel)

        try:
            if args.snapshot:
                app = Explorer(graph=read_snapshot(args.snapshot))
            else:
                app = Explorer(
                    loader=lambda: capture(args.targets, args.directory, args.option, run=runner),
                    cancel_loader=runner.cancel,
                    inspector_factory=inspector_factory,
                )
            code = app.run() or 0
            if getattr(app, "load_error", None):
                print(f"bst-tree: {app.load_error}", file=sys.stderr)
            return code
        finally:
            runner.cancel()
            for diagnostic in runner.diagnostics:
                print(diagnostic, file=sys.stderr)
    except (OSError, ValueError) as error:
        print(f"bst-tree: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
