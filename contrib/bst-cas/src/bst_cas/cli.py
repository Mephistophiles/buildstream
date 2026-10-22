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
"""Headless inspection and maintenance of the shared derived index."""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys

from .index import Index
from .config import cas_directory


def days(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be finite and nonnegative")
    return number


def search_pattern(value):
    if not value or any(c in value for c in ("\x00", "\n", "\r")) or ".." in value.split("/"):
        raise ValueError("Expected a filename or artifact-root path pattern without '..'")
    return "*/" + value if "/" not in value else "/" + value.lstrip("/")


def safe(value):
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in str(value))


def render(query, result):
    if query == "index-cache":
        lines = [f"CAS directory: {safe(result['cas_directory'])}",
                 f"Artifact refs: {safe(result['refs_directory'])}",
                 f"SQLite index:  {safe(result['index_path'])}",
                 f"Read {result['refs']} refs; indexed {result['indexed_trees']} unique trees; "
                 f"{result['reused_refs']} duplicate refs; {result['without_files']} without files.",
                 "Status: " + ("complete" if result['complete'] else "INCOMPLETE")]
        for error in result['errors']:
            lines.append(f"  {safe(error['ref'])}: {safe(error['message'])}")
        return "\n".join(lines)
    if query == "stats":
        lines = [f"CAS directory: {safe(result['cas_directory'])}",
                 f"SQLite index:  {safe(result['index_path'])}",
                 f"Indexed trees: {result['trees']}", f"Cached analyses: {result['analyses']}",
                 "These counts describe the derived index, not all objects in BuildStream CAS."]
        if not result['trees']:
            lines.append("Index is empty. Run bst-cas index-cache or search a project: bst-cas find NAME TARGET.bst")
        return "\n".join(lines)
    if query in ("find", "index"):
        lines = []
        for entry in result['entries']:
            lines.append(safe(entry['path']))
            detail = f"  {entry['kind']}, {entry['size']} bytes"
            if entry.get('link_target') is not None:
                detail += " -> " + safe(entry['link_target'])
            lines.append(detail)
            lines.append("  tree: " + safe(entry.get('tree_digest', result.get('tree_digest'))))
        lines.append(f"{len(result['entries'])} matching entries." if query == "find" else f"{len(result['entries'])} indexed entries.")
        if query == "find":
            lines.append("Scope: previously indexed metadata only; current CAS availability is not checked.")
            if not result['entries']:
                lines.append("To search project artifacts without pre-indexing: bst-cas find NAME TARGET.bst")
        return "\n".join(lines)
    return "Deleted index records: " + ", ".join(f"{k}={v}" for k, v in result['deleted'].items())


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="bst-cas",
        description="Index local BuildStream CAS trees without checkout, target execution or cache mutation.",
    )
    p.add_argument(
        "--cas-directory",
        default=os.environ.get("BST_CAS_DIRECTORY"),
        help="CAS directory override (default: discover from BuildStream user configuration)",
    )
    p.add_argument(
        "--cas-index",
        default=None,
        help="Derived SQLite index (or BST_CAS_INDEX; default XDG_CACHE_HOME/bst-cas/index.sqlite3)",
    )
    commands = p.add_subparsers(dest="command", required=True)
    build = commands.add_parser("index", help="Read and cache a REAPI Directory tree")
    build.add_argument("digest", help="Files tree digest HASH/SIZE from bst show artifact-cas-digest")
    cache = commands.add_parser("index-cache", help="Index all local artifact files trees without loading a project")
    cache.add_argument("--refs-directory", help="Artifact refs override (default: sibling artifacts/refs of CAS)")
    find = commands.add_parser("find", help="Search indexed metadata; does not infer live owners or availability")
    find.add_argument("pattern", help="Filename or case-sensitive path glob")
    find.add_argument("targets", nargs="*", help="Search live project artifacts instead of existing index metadata")
    find.add_argument("-C", "--directory")
    find.add_argument("--deps", choices=("none", "run", "build", "all"), default="all")
    find.add_argument("--option", nargs=2, action="append", default=[])
    find.add_argument("--digest", help="Restrict search to one indexed tree")
    stats = commands.add_parser("stats", help="Show cache paths and derived-index counts")
    prune = commands.add_parser("prune", help="Remove old derived records; never remove CAS objects")
    prune.add_argument("--older-than", type=days, default=30, metavar="DAYS")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    for command in (build, cache, find, stats, prune):
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    for name in ("blame", "grep", "elf", "grep-sources"):
        commands.add_parser(name, add_help=False, help="Search all locally cached source trees" if name == "grep-sources" else f"Run bst-{name} with automatic CAS discovery")
    args, forwarded = p.parse_known_args(argv)
    if args.command not in ("blame", "grep", "elf", "grep-sources") and forwarded:
        p.error("unrecognized arguments: " + " ".join(forwarded))
    try:
        if args.command == "find":
            args.pattern = search_pattern(args.pattern)
            if args.targets:
                if args.digest:
                    raise ValueError("--digest cannot be combined with live targets")
                forwarded = ["--glob", "--deps", args.deps]
                if args.directory:
                    forwarded += ["-C", args.directory]
                for name, value in args.option:
                    forwarded += ["--option", name, value]
                forwarded += ["--", args.pattern, *args.targets]
                args.command = "blame"
            elif args.directory or args.option or args.deps != "all":
                raise ValueError("Project options require TARGET arguments")
        if args.command in ("blame", "grep", "elf", "grep-sources"):
            if args.json:
                forwarded = ["--json", *forwarded]
            tool = "grep" if args.command == "grep-sources" else args.command
            executable = shutil.which("bst-" + tool)
            if executable is None:
                raise ValueError(f"bst-{tool} is missing from PATH; install the corresponding tool")
            environment = os.environ.copy()
            environment["BST_CAS_DIRECTORY"] = cas_directory(args.cas_directory)
            if args.cas_index:
                environment["BST_CAS_INDEX"] = args.cas_index
            defaults = ["--in", "cached-sources"] if args.command == "grep-sources" else (["--in", "artifacts"] if args.command == "grep" else [])
            return subprocess.call([executable, *defaults, *forwarded], env=environment)
        with Index(cas_directory(args.cas_directory), args.cas_index) as index:
            if args.command == "index-cache":
                from .cache import index_cache
                def progress(result):
                    if not args.json and sys.stderr.isatty() and result["refs"] % 100 == 0:
                        print(f"Read {result['refs']} refs, indexed {result['indexed_trees']} trees...", file=sys.stderr)
                result = index_cache(index, args.refs_directory, progress)
            elif args.command == "index":
                result = {"tree_digest": args.digest, "entries": index.tree(args.digest), "stats": index.stats()}
            elif args.command == "find":
                result = {"entries": index.find(args.pattern, args.digest), "scope": "indexed-metadata-only"}
            elif args.command == "prune":
                result = {"deleted": index.prune(args.older_than)}
            else:
                result = {**index.stats(), "cas_directory": str(index.cas_directory), "index_path": str(index.database)}
            print(json.dumps({"format_version": 1, "query": args.command, **result}, ensure_ascii=True, indent=2)
                  if args.json else render(args.command, result))
            if args.command == "index-cache" and not result["complete"]:
                return 2
            return 1 if args.command == "find" and not result["entries"] else 0
    except BrokenPipeError:
        sys.stdout = open(os.devnull, "w")
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError) as error:
        if args.json:
            print(json.dumps({"format_version": 1, "error": str(error), "complete": False}))
        else:
            print("bst-cas: " + safe(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
