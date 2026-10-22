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
"""Noninteractive bst-grep command. Exit 0 match, 1 no matches, 2 incomplete/error."""

import argparse
import base64
from contextlib import closing
import json
import os
import shutil
import sys

from . import __version__
from bst_tools_core.project import Project
from .search import rg_arguments, search, validate


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def parser():
    result = argparse.ArgumentParser(
        prog="bst-grep",
        description="Search BuildStream sources, resolved configuration or CAS artifact files, retaining element ownership.",
        epilog="Exit status: 0 matches, 1 no matches, 2 error/incomplete search, 130 interrupted. "
        "Source checkout may fetch missing sources; open workspaces are searched directly. "
        "Example: bst-grep --deps all -g '*.{c,h}' pthread app.bst",
    )
    result.add_argument("--version", action="version", version=f"bst-grep {__version__}")
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
        "--in",
        dest="area",
        choices=("sources", "config", "artifacts", "cached-sources"),
        default="sources",
        help="Search sources, config, artifacts, or all local cached-sources (default: sources)",
    )
    result.add_argument(
        "--deps",
        choices=("none", "run", "build", "all"),
        default="none",
        help="Use bst show dependency selection (default: none; build excludes targets)",
    )
    result.add_argument("-F", "--fixed-strings", action="store_true", help="Match literal text instead of a regex")
    result.add_argument("-i", "--ignore-case", action="store_true", help="Match without case sensitivity")
    result.add_argument("-w", "--word-regexp", action="store_true", help="Only match whole words")
    result.add_argument(
        "-g",
        "--glob",
        action="append",
        default=[],
        metavar="GLOB",
        help="Include/exclude relative paths using ripgrep globs; repeat as needed",
    )
    result.add_argument("-A", "--after-context", type=nonnegative, default=None, metavar="NUM")
    result.add_argument("-B", "--before-context", type=nonnegative, default=None, metavar="NUM")
    result.add_argument(
        "--context",
        type=nonnegative,
        default=0,
        metavar="NUM",
        help="Show NUM lines before/after matches; -A/-B override their respective side",
    )
    result.add_argument("--json", action="store_true", help="Stream versioned JSON Lines, ending with a summary")
    result.add_argument("--why", action="store_true", help="Show one shortest dependency path per applicable target")
    result.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Color text output (default: auto; respects NO_COLOR)",
    )
    result.add_argument(
        "pattern", metavar="PATTERN", help="Ripgrep regex or literal; use -- before leading '-' patterns"
    )
    result.add_argument("targets", nargs="*", metavar="TARGET", help="Elements, including junction-qualified names")
    result.add_argument(
        "--cas-directory",
        default=os.environ.get("BST_CAS_DIRECTORY"),
        help="Enable shared bst-cas index using this local BuildStream cas directory (or BST_CAS_DIRECTORY)",
    )
    result.add_argument("--cas-index", help="Shared SQLite index path (or BST_CAS_INDEX); requires CAS mode")
    return result


def safe_text(value):
    """Keep file contents/names from injecting terminal escapes or extra rows."""
    result = []
    for char in value:
        code = ord(char)
        if char == "\t":
            result.append("\t")
        elif char.isprintable():
            result.append(char)
        elif code < 256:
            result.append(f"\\x{code:02x}")
        else:
            result.append(f"\\u{code:04x}")
    return "".join(result)


def highlight(record):
    raw = base64.b64decode(record["text_base64"]) if "text_base64" in record else record["text"].encode("utf-8")
    pieces, position = [], 0
    for match in record["submatches"]:
        start, end = match["start"], match["end"]
        pieces.append(safe_text(raw[position:start].decode("utf-8", errors="replace")))
        pieces.append("\033[1;31m" + safe_text(raw[start:end].decode("utf-8", errors="replace")) + "\033[0m")
        position = end
    pieces.append(safe_text(raw[position:].removesuffix(b"\n").decode("utf-8", errors="replace")))
    return "".join(pieces)


class Output:
    def __init__(self, args):
        self.json = args.json
        self.why = args.why
        self.context = bool(args.context or args.before_context or args.after_context)
        self.color = not args.json and (
            args.color == "always" or (args.color == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ)
        )
        self.explained = set()
        self.previous = None

    def emit(self, record):
        if self.json:
            print(json.dumps({"format_version": 1, **record}, ensure_ascii=True), flush=True)
            return
        if record["type"] == "error":
            print(
                f"bst-grep: {safe_text(record.get('element', 'project'))}: {safe_text(record['message'])}",
                file=sys.stderr,
            )
            return
        if record["type"] == "summary":
            print(
                f"Searched {record['searched']}/{record['selected']} "
                f"{'source trees' if record.get('area') == 'cached-sources' else 'elements'}; "
                f"{record['matched_lines']} matching lines in {record['matched_files']} files; "
                f"{record['failed']} errors.",
                file=sys.stderr,
            )
            return
        element = record["element"]
        if self.why and record["type"] == "match" and element not in self.explained:
            for path in record["dependency_paths"]:
                print("# " + " -> ".join(safe_text(name) for name in path))
            self.explained.add(element)
        position = (element, record["path"], record["line"])
        if (
            self.context
            and self.previous is not None
            and (position[:2] != self.previous[:2] or position[2] > self.previous[2] + 1)
        ):
            print("--")
        self.previous = position
        prefix = f"{safe_text(element)}::{safe_text(record['path'])}"
        text = safe_text(record["text"])
        if record["type"] == "match":
            if self.color:
                prefix = "\033[36m" + prefix + "\033[0m"
                text = highlight(record)
            print(f"{prefix}:{record['line']}:{record['column']}:{text}")
        else:
            print(f"{prefix}-{record['line']}-{text}")


def _run(args, output, cas):
    for executable in ("rg", "bst"):
        if shutil.which(executable) is None:
            raise ValueError(f"{executable} is missing from PATH")
    command = rg_arguments(args)
    validate(command)
    project = Project(args.directory, args.option)
    selected = project.select(args.targets, args.deps, workspaces=args.area == "sources")
    skipped = []
    if args.area == "artifacts":
        kinds = (
            {r["name"].strip(): r["kind"].strip() for r in project.report(list(selected), ("name", "kind"))}
            if selected
            else {}
        )
        if kinds.keys() != selected.keys():
            raise ValueError("Artifact selection changed while loading; retry")
        skipped = sorted(n for n, kind in kinds.items() if kind in ("stack", "junction"))
        selected = {n: w for n, w in selected.items() if n not in skipped}
    paths = project.paths(args.targets, args.deps) if args.why else None
    matched_lines, matched_files = 0, set()
    searched = failed = 0
    prepared = ((name, None, None, None) for name in sorted(selected))
    if cas is not None:
        from bst_cas import load_artifacts

        prepared = load_artifacts(cas, project, selected)
    for index, (name, manifest, identity, problem) in enumerate(prepared, 1):
        workspace = selected[name]
        if problem:
            failed += 1
            output.emit({"type": "error", "element": name, "message": problem})
            continue
        if sys.stderr.isatty():
            action = "workspace" if workspace else "source checkout (may fetch)"
            if args.area == "config":
                action = "resolved configuration"
            elif args.area == "artifacts":
                action = "local CAS artifact files"
            print(f"[{index}/{len(selected)}] {safe_text(name)}: {action}", file=sys.stderr)
        if args.area == "artifacts":
            from bst_cas.integration import artifact_files

            contents = artifact_files(cas, project, name, loaded=(manifest, identity))
        else:
            contents = project.sources(name, workspace) if args.area == "sources" else project.configuration(name)
        try:
            with contents as (root, origin):
                dependency_paths = paths.to(name) if paths is not None else None
                if dependency_paths == []:
                    raise ValueError("Dependency graph changed while loading; retry the search")
                # Stop rg before the checkout context removes its files, even
                # when the output consumer closes the pipe or the user interrupts.
                with closing(search(root, command)) as records:
                    for record in records:
                        record.update(element=name, area=args.area, origin=origin)
                        if dependency_paths is not None:
                            record["dependency_paths"] = dependency_paths
                        if record["type"] == "match":
                            matched_lines += 1
                            matched_files.add((name, record["path"]))
                        output.emit(record)
            searched += 1
        except BrokenPipeError:
            # A consumer such as head has finished; stop the whole search rather
            # than reporting an element error and checking out further sources.
            raise
        except (OSError, ValueError) as error:
            failed += 1
            output.emit({"type": "error", "element": name, "message": str(error)})
    output.emit(
        {
            "type": "summary",
            "complete": failed == 0,
            "selected": len(selected),
            "searched": searched,
            "failed": failed,
            "matched_lines": matched_lines,
            "matched_files": len(matched_files),
            **({"cas_index": cas.stats(), "skipped": skipped} if cas is not None else {}),
        }
    )
    return 2 if failed else (0 if matched_lines else 1)


def cached_sources(args, output, cas):
    from bst_cas.cache import index_cache, source_owners
    from bst_cas.integration import artifact_files

    if args.targets or args.directory or args.option or args.why or args.deps != "none":
        raise ValueError("cached-sources searches the whole local source cache; project arguments are not supported")
    if shutil.which("rg") is None:
        raise ValueError("rg is missing from PATH")
    command = rg_arguments(args)
    validate(command)
    matched_lines, matched_files, searched = 0, set(), 0
    seen, reports = set(), []
    owners, owner_errors = source_owners(cas)

    def scan(ref, digest, entries):
        nonlocal matched_lines, searched
        identity = {"artifact-cas-digest": digest}
        with artifact_files(cas, None, ref, loaded=(entries, identity)) as (root, origin):
            with closing(search(root, command)) as records:
                for record in records:
                    known = owners.get(digest, [])
                    labels = [owner["element"] for owner in known]
                    label = ", ".join(labels) if labels else "unknown-owner [" + digest.split("/")[0][:12] + "]"
                    record.update(element=label, elements=labels, owners=known,
                                  ownership="artifact-source-digest" if known else "unknown",
                                  source_ref=ref, tree_digest=digest,
                                  area="cached-sources", origin=origin)
                    if record["type"] == "match":
                        matched_lines += 1
                        matched_files.add((digest, record["path"]))
                    output.emit(record)
        searched += 1

    for name in ("elementsources", "source_protos"):
        directory = cas.cas_directory.parent / name
        if directory.exists():
            reports.append(index_cache(cas, directory, sources=True, on_tree=scan, seen=seen))
    if not reports:
        raise ValueError("No local source metadata found (elementsources/source_protos); use bst-grep PATTERN TARGET.bst for project sources")
    errors = owner_errors + [error for report in reports for error in report["errors"]]
    for error in errors:
        output.emit({"type": "error", "element": error["ref"], "message": error["message"]})
    output.emit({"type": "summary", "area": "cached-sources", "complete": not errors,
                 "selected": searched + len(errors), "searched": searched, "failed": len(errors),
                 "matched_lines": matched_lines, "matched_files": len(matched_files),
                 "cas_index": cas.stats()})
    return 2 if errors else (0 if matched_lines else 1)


def run(args, output):
    if args.area != "cached-sources" and not args.targets:
        raise ValueError("Specify at least one TARGET, or use --in cached-sources")
    if args.area not in ("artifacts", "cached-sources"):
        # Environment configuration alone must not disable normal source search.
        if args.cas_index:
            raise ValueError("--cas-index is only used with --in artifacts")
        return _run(args, output, None)
    try:
        from bst_cas.integration import open_index
    except ImportError as error:
        raise ValueError(
            "Artifact search requires bst-cas; install ./contrib/bst-cas in the same environment"
        ) from error
    with open_index(args) as cas:
        return cached_sources(args, output, cas) if args.area == "cached-sources" else _run(args, output, cas)


def main(argv=None):
    args = parser().parse_args(argv)
    if args.before_context is None:
        args.before_context = args.context
    if args.after_context is None:
        args.after_context = args.context
    output = Output(args)
    try:
        return run(args, output)
    except BrokenPipeError:
        # Avoid a second BrokenPipeError from stdout's interpreter-exit flush.
        sys.stdout = open(os.devnull, "w")
        return 0
    except KeyboardInterrupt:
        output.emit({"type": "error", "message": "Search interrupted; results are incomplete"})
        return 130
    except (OSError, ValueError) as error:
        output.emit({"type": "error", "message": str(error)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
