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
"""Tree browsing and compatibility dispatch for older bst-tree invocations."""

import argparse
from functools import partial
import sys

from bst_tools_core.cli import require_tui
from bst_tools_core.process import CliRunner

from . import headless
from .adapter import capture
from .cli_common import command, option_argument
from .inspection import ProjectInspector
from .model import read_snapshot

LEGACY_COMMANDS = ("snapshot", "diff", "inspect", "origin", "size")


def parser():
    result = argparse.ArgumentParser(
        prog="bst-tree",
        description="Browse or print a BuildStream dependency tree.",
        epilog="Other graph and inspection tools are installed as separate commands. See bst-snapshot and bst-graph-diff.",
    )
    option_argument(result, "-o", "--option")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("browse", help="Browse a project or snapshot interactively")
    commands.add_parser("tree", help=headless.DESCRIPTIONS["tree"])
    return result


def configure_browse(parser):
    parser.description = "Browse a live BuildStream project or a saved snapshot in a terminal."
    parser.add_argument("targets", nargs="*", metavar="TARGET", help="Explicit element names for a live project")
    parser.add_argument("-C", "--directory", help="Project directory (default: current directory)")
    option_argument(parser, "-o", "--option")
    parser.add_argument("--snapshot", metavar="FILE", help="Browse a saved graph without bst or live inspection")


def run_browse(args):
    if args.snapshot and (args.targets or args.directory or args.option):
        raise ValueError("--snapshot cannot be combined with project arguments")
    if not args.snapshot and not args.targets:
        raise ValueError("browse requires targets or --snapshot")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("browse requires a terminal (TTY); use bst-snapshot or bst-graph-diff for noninteractive use")
    require_tui("bst-tree")
    from .tui import Explorer

    runner = CliRunner(quiet=True)

    def inspector_factory():
        inspection_runner = CliRunner(quiet=True)
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


def main(argv=None):
    values = sys.argv[1:] if argv is None else argv
    # REMAINDER keeps command-specific options intact, including snapshot -o FILE.
    prefix = argparse.ArgumentParser(prog="bst-tree", add_help=False)
    option_argument(prefix, "-o", "--option")
    prefix.add_argument("-h", "--help", action="store_true")
    prefix.add_argument("command", nargs="?")
    prefix.add_argument("arguments", nargs=argparse.REMAINDER)
    args = prefix.parse_args(values)
    name = args.command
    if args.help:
        parser().print_help()
        return 0
    if name not in LEGACY_COMMANDS and name not in headless.DESCRIPTIONS and name != "browse":
        parser().parse_args([name] if name else [])

    prog = f"bst-tree {name}"
    if name == "browse":
        return command(prog, configure_browse, run_browse, args.arguments, options=args.option)
    if name in headless.DESCRIPTIONS:
        configure = partial(headless.configure_parser, query=name, legacy=True)
        return command(prog, configure, headless.run_graph_query, args.arguments, intermixed=True, options=args.option)
    return run_legacy(name, prog, args.arguments, args.option)


def run_legacy(name, prog, argv, options):
    if name == "snapshot":
        from . import snapshot

        configure = partial(snapshot.configure_parser, legacy=True)
        return command(prog, configure, snapshot.run_snapshot, argv, options=options)
    if name == "diff":
        from . import diff_cli

        return command(prog, diff_cli.configure_legacy, diff_cli.run_legacy, argv, options=options)
    if name == "inspect":
        from . import headless_inspection

        return command(
            prog, headless_inspection.configure_parser, headless_inspection.run_inspection, argv, options=options
        )
    if name == "size":
        from . import size

        return command(prog, size.configure_parser, size.run_size, argv, intermixed=True, options=options)

    from . import origin

    return command(prog, origin.configure_parser, origin.run_origin, argv, intermixed=True, options=options)


if __name__ == "__main__":
    sys.exit(main())
