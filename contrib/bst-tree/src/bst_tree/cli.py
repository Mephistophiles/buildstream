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
import sys

from .adapter import capture
from .diff import DIFF_FIELDS, compare, has_changes, render_tree
from .model import read_snapshot, write_snapshot
from .inspection import ProjectInspector
from bst_tools_core.process import CliRunner
from bst_tools_core.cli import require_tui


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
            if command == "browse"
            else "Save the full dependency graph as a portable JSON snapshot."
        )
        sub = commands.add_parser(
            command,
            help=description,
            description=description,
            epilog=(
                "Examples: bst-tree browse -C /path/to/project app.bst; "
                "bst-tree browse --snapshot graph.json. Snapshot mode has no live element inspection."
                if command == "browse"
                else "Example: bst-tree snapshot -C /path/to/project app.bst -o graph.json. "
                "Existing output files are replaced atomically."
            ),
        )
        sub.add_argument(
            "targets",
            nargs="*" if command == "browse" else "+",
            metavar="TARGET",
            help="Explicit element names, including junction-qualified names; required for live projects",
        )
        sub.add_argument("-C", "--directory", help="Project directory (default: current directory)")
        sub.add_argument(
            "--option",
            nargs=2,
            action="append",
            default=[],
            metavar=("NAME", "VALUE"),
            help="Set a BuildStream project option; may be repeated (queries use strict mode)",
        )
        if command == "browse":
            sub.add_argument(
                "--snapshot",
                metavar="FILE",
                help="Browse saved JSON without bst; cannot be combined with project arguments",
            )
        else:
            sub.add_argument(
                "-o",
                "--output",
                required=True,
                metavar="FILE",
                help="Output JSON snapshot; - writes stdout, otherwise replaces an existing file",
            )
            sub.add_argument(
                "--details",
                action="store_true",
                help="Capture resolved config, variables, environment and public data for change analysis",
            )
            sub.add_argument(
                "--provenance",
                action="store_true",
                help="Save input sources alongside resolved details; requires fork bst",
            )
            sub.add_argument(
                "--history",
                action="store_true",
                help="Also capture YAML override history; requires --provenance and rebuilt fork bst",
            )
    diff = commands.add_parser(
        "diff",
        help="Compare two snapshots without loading a project",
        description="Compare saved graphs; no BuildStream installation or terminal is required.",
        epilog="Example: bst-tree diff before.json after.json --structure-only --check. "
        "Exit status: 0 success, 1 differences with --check, 2 error.",
    )
    diff.add_argument("old", help="Baseline JSON snapshot")
    diff.add_argument("new", help="Updated JSON snapshot")
    diff.add_argument(
        "--format",
        choices=("tree", "json", "tui"),
        default="tree",
        help="Output format (default: tree); tui opens an interactive change inspector",
    )
    diff.add_argument(
        "--tui",
        dest="format",
        action="store_const",
        const="tui",
        help="Open the interactive change inspector (alias for --format tui)",
    )
    diff.add_argument("--explain", action="store_true", help="Include observed inputs and dependency candidates")
    diff.add_argument(
        "--history", action="store_true", help="Expand saved YAML input histories; requires --explain or --format tui"
    )
    diff.add_argument(
        "--semantic",
        action="store_true",
        help="Compare per-target reachable dependencies and their build/runtime roles",
    )
    diff.add_argument(
        "--scope",
        choices=("all", "run", "build"),
        help="Semantic roles to compare (default: all means build and runtime)",
    )
    diff.add_argument(
        "--target",
        action="append",
        metavar="TARGET",
        help="Limit semantic comparison to a target requested in both snapshots; repeatable",
    )
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
    from .headless import configure_parser as configure_headless

    result.headless_parsers = configure_headless(commands)
    from .size import configure_parser as configure_size

    result.headless_parsers["size"] = configure_size(commands)
    from .origin import configure_parser as configure_origin

    result.headless_parsers["origin"] = configure_origin(commands)
    return result


def main(argv=None):
    arguments = parser()
    values = sys.argv[1:] if argv is None else argv
    if values and values[0] in arguments.headless_parsers:
        # Parse the selected graph command directly: older argparse versions
        # otherwise reject targets placed after options following ELEMENT.
        args = arguments.headless_parsers[values[0]].parse_intermixed_args(values[1:])
    else:
        args = arguments.parse_args(values)
    try:
        if args.command == "origin":
            from .origin import run_origin

            return run_origin(args)
        if args.command == "size":
            from .size import run_size

            return run_size(args)
        if args.command in ("tree", "rdeps", "paths"):
            from .headless import run_graph_query

            return run_graph_query(args)
        if args.command == "inspect":
            from .headless_inspection import run_inspection

            return run_inspection(args)
        if args.command == "diff":
            if args.history and not (args.explain or args.format == "tui"):
                raise ValueError("--history requires --explain or --format tui")
            old, new = read_snapshot(args.old), read_snapshot(args.new)
            fields = set(DIFF_FIELDS)
            if args.structure_only:
                fields = set()
            elif args.fields is not None:
                fields = set(args.fields)
            elif args.ignore_fields:
                fields -= set(args.ignore_fields)
            delta = compare(old, new, fields=fields)
            if (args.scope is not None or args.target is not None) and not args.semantic:
                raise ValueError("--scope and --target require --semantic")
            if args.semantic and args.reverse:
                raise ValueError("--reverse applies to structural tree output; omit it for semantic comparison")
            semantic_model = semantic_report = None
            changed = has_changes(delta)
            color = args.color == "always" or (
                args.color == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ
            )
            if args.semantic:
                from .semantic import SemanticDiff, has_semantic_changes, render_semantic

                semantic_model = SemanticDiff(old, new, args.target)
                semantic_report = semantic_model.report(
                    args.scope or "all", show_all=args.all, limit=None if args.format == "json" else 10
                )
                changed = has_semantic_changes(semantic_report)
            if args.format == "tui":
                if not sys.stdin.isatty() or not sys.stdout.isatty():
                    raise ValueError("interactive diff requires a terminal (TTY); use --explain for text or JSON")
                if args.reverse:
                    raise ValueError("--reverse applies to tree output; omit it for interactive diff")
                require_tui("bst-tree")
                from .diff_tui import DiffExplorer

                DiffExplorer(
                    old,
                    new,
                    fields=fields,
                    show_all=args.all,
                    labels=(os.path.basename(args.old), os.path.basename(args.new)),
                    semantic=args.semantic,
                    semantic_scope=args.scope or "all",
                    semantic_model=semantic_model,
                    history=args.history,
                ).run()
                return 1 if args.check and changed else 0
            if semantic_report is not None:
                explanation = None
                if args.explain:
                    from .explain import analyze, render_explanation

                    explanation = analyze(
                        old, new, fields=fields, limit=None if args.format == "json" else 10, history=args.history
                    )
                    if args.format == "json":
                        semantic_report["explanation"] = explanation
                print(
                    json.dumps(semantic_report, ensure_ascii=False, indent=2, sort_keys=True)
                    if args.format == "json"
                    else render_semantic(semantic_report, color=color)
                )
                if explanation is not None and args.format != "json":
                    print("\n" + render_explanation(explanation))
                return 1 if args.check and changed else 0
            explanation = None
            if args.explain:
                from .explain import analyze, render_explanation

                explanation = analyze(
                    old, new, fields=fields, limit=None if args.format == "json" else 10, history=args.history
                )
                if args.format == "json":
                    delta["explanation"] = explanation
            if explanation is not None and args.format == "tree":
                print(render_explanation(explanation))
                return 1 if args.check and has_changes(delta) else 0
            print(
                json.dumps(delta, ensure_ascii=False, indent=2, sort_keys=True)
                if args.format == "json"
                else render_tree(
                    old,
                    new,
                    delta,
                    args.all,
                    color,
                    reverse=args.reverse,
                )
            )
            return 1 if args.check and has_changes(delta) else 0
        if args.command == "snapshot":
            graph = capture(
                args.targets,
                args.directory,
                args.option,
                detailed=args.details,
                provenance=args.provenance,
                history=args.history,
            )
            if args.output == "-":
                print(json.dumps(graph.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            else:
                write_snapshot(graph, args.output)
            return 0
        if args.snapshot and (args.targets or args.directory or args.option):
            arguments.error("--snapshot cannot be combined with project arguments")
        if not args.snapshot and not args.targets:
            arguments.error("browse requires targets or --snapshot")
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError("browse requires a terminal (TTY); use snapshot or diff for noninteractive use")
        require_tui("bst-tree")
        from .tui import Explorer

        runner = CliRunner(quiet=True)

        def inspector_factory():
            inspection_runner = CliRunner(quiet=True)
            return ProjectInspector(
                args.directory, args.option, run=inspection_runner, cancel=inspection_runner.cancel
            )

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
