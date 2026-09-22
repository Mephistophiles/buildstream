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
import subprocess
import sys
import threading

from .adapter import capture
from .diff import compare, has_changes, render_tree
from .model import read_snapshot, write_snapshot


def parser():
    result = argparse.ArgumentParser(prog="bst-tree")
    commands = result.add_subparsers(dest="command", required=True)
    for command in ("browse", "snapshot"):
        sub = commands.add_parser(command)
        sub.add_argument("targets", nargs="*" if command == "browse" else "+")
        sub.add_argument("-C", "--directory")
        sub.add_argument("--option", nargs=2, action="append", default=[], metavar=("NAME", "VALUE"))
        if command == "browse":
            sub.add_argument("--snapshot")
        else:
            sub.add_argument("-o", "--output", required=True)
    diff = commands.add_parser("diff")
    diff.add_argument("old")
    diff.add_argument("new")
    diff.add_argument("--format", choices=("tree", "json"), default="tree")
    diff.add_argument("--all", action="store_true", help="Include unchanged branches")
    diff.add_argument("--check", action="store_true", help="Exit 1 when snapshots differ")
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
            delta = compare(old, new)
            print(
                json.dumps(delta, ensure_ascii=False, indent=2, sort_keys=True)
                if args.format == "json"
                else render_tree(old, new, delta, args.all, sys.stdout.isatty())
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
        try:
            if args.snapshot:
                app = Explorer(graph=read_snapshot(args.snapshot))
            else:
                app = Explorer(
                    loader=lambda: capture(args.targets, args.directory, args.option, run=runner),
                    cancel_loader=runner.cancel,
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
