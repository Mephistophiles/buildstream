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
"""CLI for live artifact refs or offline checkout archives."""

import argparse
import json
import math
import os
import sys


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of bytes")
    return number


def configure_parser(commands):
    sub = commands.add_parser(
        "artifact-diff",
        help="Compare files in two artifacts; optional abidiff and diffoscope",
        description="Compare artifact refs or two saved checkout tar archives, without builds or integration.",
        epilog="Examples: bst-tree artifact-diff PROJECT/ELEMENT/OLD_KEY app.bst -C /project --tui; "
        "bst-tree artifact-diff before.tar after.tar --archives --abidiff --diffoscope --check.",
    )
    sub.add_argument("old", help="Old artifact ref / element name (or tar archive with --archives)")
    sub.add_argument("new", help="New artifact ref / element name (or tar archive with --archives)")
    sub.add_argument("--archives", action="store_true", help="Compare two local checkout tar archives without bst")
    sub.add_argument("-C", "--directory", help="Project directory for live artifact checkout")
    sub.add_argument(
        "--option",
        nargs=2,
        action="append",
        default=[],
        metavar=("NAME", "VALUE"),
        help="BuildStream project option; repeatable",
    )
    sub.add_argument(
        "--path", action="append", default=[], help="Limit comparison to an exact artifact-relative path; repeatable"
    )
    sub.add_argument("--all", action="store_true", help="Include unchanged entries")
    sub.add_argument("--format", choices=("text", "json", "tui"), default="text", help="Output format (default: text)")
    sub.add_argument(
        "--tui", dest="format", action="store_const", const="tui", help="Open the interactive file comparison"
    )
    sub.add_argument(
        "--abidiff", action="store_true", help="Analyze changed paired ELF files, or explicit --path ELF pairs"
    )
    sub.add_argument(
        "--diffoscope", action="store_true", help="Analyze both archives, or paired regular files selected with --path"
    )
    sub.add_argument(
        "--diffoscope-html",
        metavar="FILE",
        help="Atomically write a diffoscope HTML report; implies --diffoscope, at most one --path",
    )
    sub.add_argument(
        "--debug-info-dir-old",
        action="append",
        default=[],
        metavar="DIR",
        help="External old debug-info root for abidiff; repeatable",
    )
    sub.add_argument(
        "--debug-info-dir-new",
        action="append",
        default=[],
        metavar="DIR",
        help="External new debug-info root for abidiff; repeatable",
    )
    sub.add_argument(
        "--abidiff-suppression",
        action="append",
        default=[],
        metavar="FILE",
        help="Explicit abidiff suppression file; repeatable",
    )
    sub.add_argument(
        "--timeout",
        type=positive_float,
        default=120,
        metavar="SECONDS",
        help="Timeout per checkout/analyzer command (default: 120)",
    )
    sub.add_argument(
        "--report-limit",
        type=positive_int,
        default=1024 * 1024,
        metavar="BYTES",
        help="Captured bytes per tool stream / diffoscope report limit (default: 1048576)",
    )
    sub.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Color text summaries (default: auto; respects NO_COLOR)",
    )
    sub.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 for selected file or analyzer differences; requested analyzer errors exit 2",
    )


def run_artifact_diff(args):
    from .artifacts import (
        ArtifactComparison,
        archive_name,
        has_analysis_errors,
        has_artifact_changes,
        render_artifacts,
    )

    if args.archives and (args.directory or args.option):
        raise ValueError("--archives cannot be combined with project arguments")
    if args.diffoscope_html and len(set(args.path)) > 1:
        raise ValueError("--diffoscope-html requires at most one --path")
    if args.format == "tui" and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise ValueError("artifact comparison TUI requires a terminal (TTY); use --format text or json")
    paths = list(dict.fromkeys(archive_name(p) for p in args.path))
    session = ArtifactComparison(
        args.old,
        args.new,
        archives=args.archives,
        directory=args.directory,
        options=args.option,
        paths=paths,
        show_all=args.all,
        timeout=args.timeout,
        report_limit=args.report_limit,
        debug_old=args.debug_info_dir_old,
        debug_new=args.debug_info_dir_new,
        suppressions=args.abidiff_suppression,
    )

    def load(progress=lambda text: None):
        session.load(progress)
        if args.diffoscope or args.diffoscope_html:
            if paths and any(not session.pair(path) for path in paths):
                raise ValueError(
                    "Selected-file diffoscope analysis requires regular files on both sides; omit --path to compare whole archives"
                )
        if args.abidiff:
            for path in paths or session.report()["elf_pairs"]:
                if session.pair(path, elf=True):
                    progress("Running abidiff: " + path)
                    session.analyze("abidiff", path)
        if args.diffoscope or args.diffoscope_html:
            for path in paths or [None]:
                progress("Running diffoscope: " + (path or "whole artifact"))
                session.analyze("diffoscope", path, html=args.diffoscope_html)

    try:
        if args.format == "tui":
            from .artifact_tui import ArtifactDiffApp

            app = ArtifactDiffApp(session, loader=load)
            code = app.run() or 0
            if getattr(app, "load_error", None):
                print("bst-tree: " + app.load_error, file=sys.stderr)
            if code:
                return code
        else:
            load()
        report = session.report()
        report["requested_analyses"] = (["abidiff"] if args.abidiff else []) + (
            ["diffoscope"] if args.diffoscope or args.diffoscope_html else []
        )
        if args.format != "tui":
            if paths:
                report["text_diffs"] = {path: session.text_diff(path) for path in paths}
            if args.format == "json":
                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                color = args.color == "always" or (
                    args.color == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ
                )
                print(render_artifacts(report, color=color))
                for path, text in list(report.get("text_diffs", {}).items())[:5]:
                    lines = text.splitlines()
                    print("\n" + path + "\n" + "\n".join(lines[:60]))
                    if len(lines) > 60:
                        print("[Preview abbreviated; use --format json or --tui.]")
        if has_analysis_errors(report):
            return 2
        return 1 if args.check and has_artifact_changes(report) else 0
    finally:
        session.close()
