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
import sys

from .index import Index


def days(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be finite and nonnegative")
    return number


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="bst-cas",
        description="Index local BuildStream CAS trees without checkout, target execution or cache mutation.",
    )
    p.add_argument(
        "--cas-directory",
        default=os.environ.get("BST_CAS_DIRECTORY"),
        help="BuildStream cas directory (or BST_CAS_DIRECTORY); explicit, not guessed",
    )
    p.add_argument(
        "--cas-index",
        default=None,
        help="Derived SQLite index (or BST_CAS_INDEX; default XDG_CACHE_HOME/bst-cas/index.sqlite3)",
    )
    commands = p.add_subparsers(dest="command", required=True)
    build = commands.add_parser("index", help="Read and cache a REAPI Directory tree")
    build.add_argument("digest", help="Files tree digest HASH/SIZE from bst show artifact-cas-digest")
    find = commands.add_parser("find", help="Search indexed metadata; does not infer live owners or availability")
    find.add_argument("pattern", help="Case-sensitive absolute-path glob")
    find.add_argument("--digest", help="Restrict search to one indexed tree")
    commands.add_parser("stats", help="Count indexed trees and per-file analyses")
    prune = commands.add_parser("prune", help="Remove old derived records; never remove CAS objects")
    prune.add_argument("--older-than", type=days, default=30, metavar="DAYS")
    args = p.parse_args(argv)
    try:
        with Index(args.cas_directory, args.cas_index) as index:
            if args.command == "index":
                result = {"tree_digest": args.digest, "entries": index.tree(args.digest), "stats": index.stats()}
            elif args.command == "find":
                result = {"entries": index.find(args.pattern, args.digest), "scope": "indexed-metadata-only"}
            elif args.command == "prune":
                result = {"deleted": index.prune(args.older_than)}
            else:
                result = index.stats()
            print(json.dumps({"format_version": 1, "query": args.command, **result}, ensure_ascii=True, indent=2))
            return 0
    except BrokenPipeError:
        sys.stdout = open(os.devnull, "w")
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError) as error:
        print(json.dumps({"format_version": 1, "error": str(error), "complete": False}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
