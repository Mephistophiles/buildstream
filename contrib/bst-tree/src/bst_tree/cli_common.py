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
"""Argument parsing and exit-status handling shared by the command entry points."""

import argparse
import sys

from bst_tools_core.cli import positive_float, positive_int


def option_argument(parser, *flags):
    parser.add_argument(
        *flags,
        nargs=2,
        action="append",
        default=[],
        metavar=("NAME", "VALUE"),
        help="BuildStream project option; repeatable",
    )


def project_arguments(parser, *, formats=("text", "json"), default_format="text"):
    parser.add_argument("-C", "--directory", help="BuildStream project directory (default: current directory)")
    option_argument(parser, "-o", "--option")
    parser.add_argument(
        "--timeout", type=positive_float, default=120, metavar="SECONDS", help="Timeout per bst command (default: 120)"
    )
    parser.add_argument(
        "--output-limit",
        type=positive_int,
        default=32 * 1024 * 1024,
        metavar="BYTES",
        help="Maximum captured bytes per bst stream (default: 32 MiB); overflow is an error",
    )
    parser.add_argument(
        "--format",
        choices=formats,
        default=default_format,
        help=f"Output format (default: {default_format})",
    )


def invoke(prog, handler, *args):
    try:
        return handler(*args)
    except (OSError, ValueError) as error:
        print(f"{prog}: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def command(prog, configure, handler, argv=None, *, intermixed=False, options=()):
    parser = argparse.ArgumentParser(prog=prog)
    configure(parser)
    parse = parser.parse_intermixed_args if intermixed else parser.parse_args
    args = parse(argv)
    if options:
        if not hasattr(args, "option"):
            print(f"{prog}: --option is not supported for offline comparisons", file=sys.stderr)
            return 2
        args.option = list(options) + args.option
    return invoke(prog, handler, args)
