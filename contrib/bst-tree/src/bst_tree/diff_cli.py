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
"""Standalone snapshot comparisons and the compatibility diff command."""

import json
import os
import sys

from bst_tools_core.cli import require_tui

from .cli_common import command
from .diff import DIFF_FIELDS, compare, has_changes, render_flat, render_tree
from .model import Graph, read_snapshot


def comparison_arguments(parser, formats):
    parser.add_argument("old", help="Baseline JSON snapshot (use - for stdin)")
    parser.add_argument("new", help="Updated JSON snapshot (use - for stdin)")
    parser.add_argument("--format", choices=formats, help=f"Output format (default: {formats[0]})")
    parser.add_argument(
        "--tui", dest="format", action="store_const", const="tui", help="Open the interactive change inspector"
    )
    parser.add_argument("--check", action="store_true", help="Exit 1 if the comparison has changes")


def field_arguments(parser):
    fields = parser.add_mutually_exclusive_group()
    fields.add_argument(
        "--fields", nargs="+", choices=DIFF_FIELDS, help="Compare only these fields plus graph structure"
    )
    fields.add_argument("--ignore-fields", nargs="+", choices=DIFF_FIELDS, help="Ignore these fields")
    fields.add_argument("--structure-only", action="store_true", help="Compare only nodes, typed edges and targets")


def semantic_arguments(parser):
    parser.add_argument("--scope", choices=("all", "run", "build"), help="Dependency roles to compare (default: all)")
    parser.add_argument(
        "--target", action="append", metavar="TARGET", help="Compare a target requested in both snapshots; repeatable"
    )


def color_argument(parser):
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Color text output (default: auto; respects NO_COLOR)",
    )


def configure_graph(parser):
    parser.description = "Compare element fields, typed dependency edges and targets in two snapshots."
    comparison_arguments(parser, ("flat", "tree", "json", "tui"))
    field_arguments(parser)
    color_argument(parser)
    parser.add_argument("--all", action="store_true", help="Include unchanged elements in text output")
    parser.add_argument("--reverse", action="store_true", help="Render the tree from dependencies to consumers")


def configure_closure(parser):
    parser.description = "Compare reachable dependencies and their build/runtime roles for each common target."
    comparison_arguments(parser, ("text", "json", "tui"))
    semantic_arguments(parser)
    color_argument(parser)
    parser.add_argument("--all", action="store_true", help="Include unchanged dependencies")


def configure_explain(parser):
    parser.description = "Explain observed input changes and dependency candidates for changed element cache keys."
    comparison_arguments(parser, ("text", "json", "tui"))
    field_arguments(parser)
    parser.add_argument("--history", action="store_true", help="Include saved YAML input histories")


def configure_legacy(parser):
    configure_graph(parser)
    semantic_arguments(parser)
    parser.add_argument("--semantic", action="store_true", help="Compare reachable dependencies per target")
    parser.add_argument("--explain", action="store_true", help="Include observed inputs and dependency candidates")
    parser.add_argument("--history", action="store_true", help="Expand saved histories with --explain or --tui")


def snapshots(args):
    if args.old == args.new == "-":
        raise ValueError("Only one snapshot may be read from stdin")

    def load(path):
        return Graph.from_dict(json.load(sys.stdin)) if path == "-" else read_snapshot(path)

    return load(args.old), load(args.new)


def selected_fields(args):
    if args.structure_only:
        return set()
    if args.fields is not None:
        return set(args.fields)
    return set(DIFF_FIELDS) - set(args.ignore_fields or ())


def output_json(report):
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def use_color(args):
    return args.color == "always" or (args.color == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ)


def open_tui(args, old, new, fields, *, semantic_model=None):
    if not sys.stdin.isatty() or not sys.stdout.isatty() or "-" in (args.old, args.new):
        raise ValueError("interactive diff requires a terminal (TTY) and two snapshot files")
    require_tui("bst-tree")
    from .diff_tui import DiffExplorer

    DiffExplorer(
        old,
        new,
        fields=fields,
        show_all=getattr(args, "all", False),
        labels=(os.path.basename(args.old), os.path.basename(args.new)),
        semantic=semantic_model is not None,
        semantic_scope=getattr(args, "scope", None) or "all",
        semantic_model=semantic_model,
        history=getattr(args, "history", False),
    ).run()


def explain(args, old, new, fields):
    from .explain import analyze

    return analyze(old, new, fields=fields, limit=None if args.format == "json" else 10, history=args.history)


def print_explanation(report):
    from .explain import render_explanation

    print(render_explanation(report))


def run_graph(args, *, with_explanation=False):
    args.format = args.format or ("tree" if args.reverse else "flat")
    if args.reverse and args.format in ("flat", "tui"):
        raise ValueError("--reverse applies to tree output; use --format tree")
    old, new = snapshots(args)
    fields = selected_fields(args)
    delta = compare(old, new, fields=fields)
    if args.format == "tui":
        open_tui(args, old, new, fields)
    elif args.format == "json":
        if with_explanation:
            delta["explanation"] = explain(args, old, new, fields)
        output_json(delta)
    elif args.format == "tree":
        if with_explanation:
            print_explanation(explain(args, old, new, fields))
        else:
            print(render_tree(old, new, delta, args.all, use_color(args), reverse=args.reverse))
    else:
        print(render_flat(old, new, delta, args.all, use_color(args)))
        if with_explanation:
            print()
            print_explanation(explain(args, old, new, fields))
    return 1 if args.check and has_changes(delta) else 0


def run_closure(args, *, legacy_fields=None, with_explanation=False):
    from .semantic import SemanticDiff, has_semantic_changes, render_semantic

    old, new = snapshots(args)
    model = SemanticDiff(old, new, args.target)
    report = model.report(args.scope or "all", show_all=args.all, limit=None if args.format == "json" else 10)
    if args.format == "tui":
        open_tui(args, old, new, set(DIFF_FIELDS) if legacy_fields is None else legacy_fields, semantic_model=model)
    elif args.format == "json":
        if with_explanation:
            report["explanation"] = explain(args, old, new, legacy_fields)
        output_json(report)
    else:
        print(render_semantic(report, color=use_color(args)))
        if with_explanation:
            print()
            print_explanation(explain(args, old, new, legacy_fields))
    return 1 if args.check and has_semantic_changes(report) else 0


def run_explain(args):
    old, new = snapshots(args)
    fields = selected_fields(args)
    if args.format == "tui":
        open_tui(args, old, new, fields)
    else:
        report = explain(args, old, new, fields)
        if args.format == "json":
            output_json(report)
        else:
            print_explanation(report)
    return 1 if args.check and has_changes(compare(old, new, fields=fields)) else 0


def run_legacy(args):
    if args.history and not (args.explain or args.format == "tui"):
        raise ValueError("--history requires --explain or --format tui")
    if args.semantic or args.scope is not None:
        if args.reverse:
            raise ValueError("--reverse applies to structural tree output; omit it for semantic comparison")
        return run_closure(args, legacy_fields=selected_fields(args), with_explanation=args.explain)
    if args.target is not None:
        raise ValueError("--target requires --semantic or --scope")
    return run_graph(args, with_explanation=args.explain)


def graph_main(argv=None):
    return command("bst-graph-diff", configure_graph, run_graph, argv)


def closure_main(argv=None):
    return command("bst-closure-diff", configure_closure, run_closure, argv)


def explain_main(argv=None):
    return command("bst-key-explain", configure_explain, run_explain, argv)
