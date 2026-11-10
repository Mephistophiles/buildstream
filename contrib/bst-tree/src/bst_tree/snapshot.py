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
"""Capture a portable snapshot from a live BuildStream project."""

import json

from bst_tools_core.cli import positive_float, positive_int

from .adapter import capture
from .cli_common import command, option_argument
from .model import write_snapshot


def configure_parser(parser, *, legacy=False):
    parser.description = "Capture the full dependency graph as a portable JSON snapshot."
    parser.add_argument(
        "targets", nargs="+", metavar="TARGET", help="Element names, including junction-qualified names"
    )
    parser.add_argument("-C", "--directory", help="BuildStream project directory (default: current directory)")
    option_argument(parser, "--option")
    parser.add_argument(
        "-o",
        "--output",
        required=legacy,
        default="-",
        metavar="FILE",
        help="Output file, replaced atomically. Use - for stdout" + ("" if legacy else " (default)"),
    )
    parser.add_argument(
        "--timeout", type=positive_float, default=300, metavar="SECONDS", help="Timeout per bst command (default: 300)"
    )
    parser.add_argument(
        "--output-limit",
        type=positive_int,
        metavar="BYTES",
        help="Limit per bst output stream (default: 256 MiB with provenance, 32 MiB otherwise)",
    )
    parser.add_argument(
        "--details", action="store_true", help="Capture resolved configuration, variables and environment"
    )
    parser.add_argument(
        "--provenance", action="store_true", help="Capture YAML input sources; requires the provenance backend"
    )
    parser.add_argument("--history", action="store_true", help="Capture YAML override history; requires --provenance")


def run_snapshot(args):
    graph = capture(
        args.targets,
        args.directory,
        args.option,
        detailed=args.details,
        provenance=args.provenance,
        output_limit=args.output_limit,
        history=args.history,
        timeout=args.timeout,
    )
    if args.output == "-":
        print(json.dumps(graph.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        write_snapshot(graph, args.output)
    return 0


def main(argv=None):
    return command("bst-snapshot", configure_parser, run_snapshot, argv)
