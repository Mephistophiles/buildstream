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
"""Deterministic graph queries without a terminal or Textual."""

from collections import deque
import json
import sys

from .adapter import capture
from bst_tools_core.process import CliRunner
from bst_tools_core.cli import positive_float, positive_int
from .model import Graph, read_snapshot


def project_arguments(sub):
    sub.add_argument("-C", "--directory", help="BuildStream project directory (default: current directory)")
    sub.add_argument(
        "--option", nargs=2, action="append", default=[], metavar=("NAME", "VALUE"), help="Project option; repeatable"
    )
    sub.add_argument(
        "--timeout", type=positive_float, default=120, metavar="SECONDS", help="Timeout per bst command (default: 120)"
    )
    sub.add_argument(
        "--output-limit",
        type=positive_int,
        default=32 * 1024 * 1024,
        metavar="BYTES",
        help="Maximum captured bytes per bst stream (default: 32 MiB); overflow is an error",
    )
    sub.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text); no terminal required",
    )


def configure_parser(commands):
    parsers = {}
    descriptions = {
        "tree": "Print a dependency tree from a project or snapshot",
        "rdeps": "Find an element's consumers within the loaded graph",
        "paths": "Show one shortest dependency path per requested target",
    }
    for command, description in descriptions.items():
        sub = commands.add_parser(
            command,
            help=description,
            description=description,
            epilog=f"Example: bst-tree {command} "
            + ("lib.bst " if command != "tree" else "")
            + "app.bst -C /project --format json. Use --snapshot graph.json instead of live targets.",
        )
        if command != "tree":
            sub.add_argument("element", help="Exact element name to query in the loaded graph")
        sub.add_argument(
            "targets", nargs="*", metavar="TARGET", help="Explicit graph roots; required for live projects"
        )
        sub.add_argument(
            "--snapshot",
            metavar="FILE",
            help="Read a saved graph without bst (- reads stdin); cannot be combined with project arguments or live targets",
        )
        sub.add_argument(
            "--scope",
            choices=("all", "run", "build"),
            default="all",
            help="Dependency scope; matches browse semantics (default: all)",
        )
        if command == "tree":
            sub.add_argument(
                "--match",
                metavar="TEXT",
                help="Case-insensitive element-name substring; retain matching nodes and ancestor context",
            )
        if command == "rdeps":
            sub.add_argument("--direct", action="store_true", help="Only immediate consumers (default: transitive)")
        project_arguments(sub)
        sub.set_defaults(command=command)
        parsers[command] = sub
    from .headless_inspection import configure_parser as configure_inspection

    configure_inspection(commands)
    return parsers


def subset(graph, names, roots, edges=None):
    return Graph(
        list(roots),
        {n: graph.nodes[n] for n in sorted(names)},
        {
            e: kinds
            for e, kinds in graph.edges.items()
            if e[0] in names and e[1] in names and (edges is None or e in edges)
        },
        graph.metadata,
        {n: graph.details[n] for n in sorted(names)} if graph.details is not None else None,
    )


def query_graph(graph, command, *, scope="all", element=None, match=None, direct=False):
    if element is not None and element not in graph.nodes:
        raise ValueError(f"Element is absent from the loaded graph: {element}")
    view = graph.scoped(scope)
    report = {"format_version": 1, "query": command, "scope": scope, "requested_targets": sorted(graph.targets)}
    if element is not None:
        report["element"] = element
        report["in_scope"] = element in view.nodes
    if command == "paths":
        report["paths"] = [
            {
                "target": path[0],
                "nodes": path,
                "edges": [{"from": a, "to": b, "types": sorted(view.edges[a, b])} for a, b in zip(path, path[1:])],
            }
            for path in view.paths_to(element)
        ]
        return report
    roots = list(view.targets)
    if command == "rdeps":
        report["direct"] = direct
        names = {element} if element in view.nodes else set()
        reverse = view.adjacency(reverse=True)
        queue = deque(sorted(names))
        while queue:
            child = queue.popleft()
            for parent, _ in reverse[child]:
                if parent not in names:
                    names.add(parent)
                    if not direct:
                        queue.append(parent)
        report["consumers"] = sorted(names - {element})
        roots = [element] if names else []
        edges = {edge for edge in view.edges if edge[1] == element} if direct else None
        view = subset(view, names, roots, edges)
    elif match is not None:
        matches = sorted(n for n in view.nodes if match.casefold() in n.casefold())
        report["match"], report["matches"] = match, matches
        names, selected_edges = set(matches), set()
        queue = deque(matches)
        reverse = view.adjacency(reverse=True)
        while queue:
            child = queue.popleft()
            for parent, _ in reverse[child]:
                selected_edges.add((parent, child))
                if parent not in names:
                    names.add(parent)
                    queue.append(parent)
        roots = [n for n in roots if n in names]
        view = subset(view, names, roots, selected_edges)
    report["roots"] = roots
    report["graph"] = view.as_dict()
    return report


def render_graph(report):
    if report["query"] == "paths":
        return "\n".join(" -> ".join(p["nodes"]) for p in report["paths"]) or "No paths in the selected scope."
    graph = report["graph"]
    reverse = report["query"] == "rdeps"
    adjacency = {n: [] for n in graph["nodes"]}
    for edge in graph["edges"]:
        parent, child = (edge["to"], edge["from"]) if reverse else (edge["from"], edge["to"])
        adjacency[parent].append((child, edge["types"]))
    lines = [f"{'Consumers' if reverse else 'Dependencies'} (scope={report['scope']}; {len(adjacency)} elements):"]
    if not adjacency:
        lines.append("No matching elements in the selected scope.")
    if reverse and not report["consumers"] and adjacency:
        lines.append("No consumers in the loaded graph and selected scope.")
    expanded = set()
    # Expand each subtree once, even for shared dependencies and cycles. The
    # original directed edges remain in JSON, which never repeats subtrees.
    for root in report["roots"] + sorted(adjacency):
        if root in expanded:
            continue
        stack = [(root, 0, ())]
        while stack:
            name, depth, kinds = stack.pop()
            label = "  " * depth + name + (" [" + "/".join(kinds) + "]" if kinds else "")
            if name in expanded:
                lines.append(label + " (already shown)")
                continue
            lines.append(label)
            expanded.add(name)
            for child, types in reversed(sorted(adjacency[name])):
                stack.append((child, depth + 1, types))
    return "\n".join(lines)


def run_graph_query(args):
    if args.snapshot and (args.targets or args.directory or args.option):
        raise ValueError("--snapshot cannot be combined with live targets or project arguments")
    if not args.snapshot and not args.targets:
        raise ValueError(args.command + " requires graph targets or --snapshot")
    runner = CliRunner(args.timeout, args.output_limit)
    try:
        if args.snapshot == "-":
            graph = Graph.from_dict(json.load(sys.stdin))
        else:
            graph = (
                read_snapshot(args.snapshot)
                if args.snapshot
                else capture(args.targets, args.directory, args.option, run=runner)
            )
        report = query_graph(
            graph,
            args.command,
            scope=args.scope,
            element=getattr(args, "element", None),
            match=getattr(args, "match", None),
            direct=getattr(args, "direct", False),
        )
        print(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
            if args.format == "json"
            else render_graph(report)
        )
        return 0
    finally:
        runner.cancel()
