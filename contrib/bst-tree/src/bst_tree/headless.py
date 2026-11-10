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
from dataclasses import replace
from functools import partial
import json
import sys

from .adapter import capture
from bst_tools_core.process import CliRunner
from bst_tools_core.cli import positive_int
from .cli_common import command, project_arguments
from .model import Graph, read_snapshot

DESCRIPTIONS = {
    "tree": "Print a dependency tree from a project or snapshot",
    "rdeps": "Find an element's consumers within the loaded graph",
    "paths": "Show one shortest dependency path per requested target",
    "path": "Show a shortest dependency path between two elements",
    "chains": "List simple dependency chains between two elements",
    "rebuild": "Select elements on dependency paths from a target to a changed element",
}


def configure_parser(parser, query, *, legacy=False):
    parser.description = DESCRIPTIONS[query]
    pair = query in ("path", "chains", "rebuild")
    if pair:
        parser.add_argument("source", metavar="FROM", help="Exact starting element name")
        parser.add_argument("element", metavar="TO", help="Exact destination element name")
        parser.set_defaults(targets=[])
    else:
        if query != "tree":
            parser.add_argument("element", help="Exact element name to query in the loaded graph")
        parser.add_argument("targets", nargs="*", metavar="TARGET", help="Explicit graph roots for live projects")

    parser.add_argument("--snapshot", metavar="FILE", help="Read a saved graph without bst (- reads stdin)")
    parser.add_argument(
        "--scope",
        choices=("all", "run", "build"),
        default="all",
        help="Dependency scope (default: all)",
    )
    if query == "tree":
        parser.add_argument("--match", metavar="TEXT", help="Match names and retain their ancestor context")
    if query == "rdeps":
        parser.add_argument("--direct", action="store_true", help="Only immediate consumers (default: transitive)")
    if query == "chains" or (query == "rebuild" and legacy):
        parser.add_argument(
            "--max-chains",
            type=positive_int,
            default=1000,
            metavar="N",
            help="Maximum chains to print (default: 1000; truncation exits 3)",
        )
    if query == "rebuild":
        formats = ("elements", "chains", "json") if legacy else ("elements", "json")
        project_arguments(parser, formats=formats, default_format="elements")
    else:
        project_arguments(parser)
    parser.set_defaults(command=query)


def query_main(query, argv=None):
    name = "bst-rebuild-select" if query == "rebuild" else f"bst-{query}"
    return command(name, partial(configure_parser, query=query), run_graph_query, argv, intermixed=True)


main_rdeps = partial(query_main, "rdeps")
main_paths = partial(query_main, "paths")
main_path = partial(query_main, "path")
main_chains = partial(query_main, "chains")
main_rebuild = partial(query_main, "rebuild")


def dependency_chains(graph, source, destination, limit):
    """Enumerate simple paths iteratively, pruning nodes that cannot reach TO."""
    if limit < 1:
        raise ValueError("max_chains must be positive")
    if destination not in graph.nodes:
        return [], False
    reverse = graph.adjacency(reverse=True)
    reachable = {destination}
    queue = deque([destination])
    while queue:
        for parent, _ in reverse[queue.popleft()]:
            if parent not in reachable:
                reachable.add(parent)
                queue.append(parent)
    if source not in reachable:
        return [], False
    adjacency = graph.adjacency()
    path, active, results = [source], {source}, []
    stack = [iter(adjacency[source])]
    while stack:
        if path[-1] == destination:
            if len(results) == limit:
                return results, True
            results.append(list(path))
            child = None
        else:
            child = next(stack[-1], None)
        if child is None:
            stack.pop()
            active.remove(path.pop())
        elif child[0] in reachable and child[0] not in active:
            path.append(child[0])
            active.add(child[0])
            stack.append(iter(adjacency[child[0]]))
    return results, False


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
        {n: graph.provenance[n] for n in sorted(names)} if graph.provenance is not None else None,
    )


def query_graph(graph, command, *, scope="all", element=None, match=None, direct=False, source=None, max_chains=1000):
    if element is not None and element not in graph.nodes:
        raise ValueError(f"Element is absent from the loaded graph: {element}")
    if command in ("path", "chains", "rebuild"):
        if source not in graph.nodes:
            raise ValueError(f"Element is absent from the loaded graph: {source}")
        # Scope must be relative to the selected source, not snapshot roots.
        graph = replace(graph, targets=[source])
    view = graph.scoped(scope)
    report = {"format_version": 1, "query": command, "scope": scope, "requested_targets": sorted(graph.targets)}
    if element is not None:
        report["element"] = element
        report["in_scope"] = element in view.nodes
    if command in ("path", "chains", "rebuild"):
        report["source"] = source
    if command == "rebuild":
        from .rebuild import rebuild_report

        report.update(rebuild_report(view, source, element))
        return report
    if command in ("paths", "path", "chains"):
        paths = view.paths_to(element)
        if command == "chains":
            paths, truncated = dependency_chains(view, source, element, max_chains)
            report.update(max_chains=max_chains, truncated=truncated)
        report["paths"] = [
            {
                "target": path[0],
                "nodes": path,
                "edges": [{"from": a, "to": b, "types": sorted(view.edges[a, b])} for a, b in zip(path, path[1:])],
            }
            for path in paths
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
    if report["query"] == "chains":
        return "\n".join(" -> ".join(p["nodes"]) for p in report["paths"])
    if report["query"] in ("paths", "path"):
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
    if not args.snapshot and not args.targets and args.command not in ("path", "chains", "rebuild"):
        raise ValueError(args.command + " requires graph targets or --snapshot")
    targets = (
        list(dict.fromkeys([args.source, args.element]))
        if args.command in ("path", "chains", "rebuild")
        else args.targets
    )
    runner = CliRunner(args.timeout, args.output_limit)
    try:
        if args.snapshot == "-":
            graph = Graph.from_dict(json.load(sys.stdin))
        else:
            graph = (
                read_snapshot(args.snapshot)
                if args.snapshot
                else capture(targets, args.directory, args.option, run=runner)
            )
        report = query_graph(
            graph,
            args.command,
            scope=args.scope,
            element=getattr(args, "element", None),
            match=getattr(args, "match", None),
            direct=getattr(args, "direct", False),
            source=getattr(args, "source", None),
            max_chains=getattr(args, "max_chains", 1000),
        )
        if args.command == "rebuild":
            from .rebuild import render_rebuild

            return render_rebuild(report, graph, args)
        output = (
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
            if args.format == "json"
            else render_graph(report)
        )
        if output:
            print(output)
        if report.get("truncated"):
            print("bst-tree: chain limit reached; increase --max-chains for more results", file=sys.stderr)
            return 3
        return 1 if args.command in ("path", "chains", "rebuild") and not report["paths"] else 0
    finally:
        runner.cancel()
