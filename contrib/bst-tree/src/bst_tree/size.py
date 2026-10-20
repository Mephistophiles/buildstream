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
"""Logical artifact/closure sizes with explicit content-unique and root-exclusive metrics."""

from collections import Counter
import json
import os
from pathlib import Path
import tarfile
import tempfile
import threading

from .adapter import capture
from bst_tools_core.project import command_base, Project
from bst_tools_core.archive import Archive, FILE_KINDS
from bst_tools_core.cli import positive_int
from bst_tools_core.process import CliRunner
from .headless import project_arguments
from .model import Graph, read_snapshot


def configure_parser(commands):
    p = commands.add_parser(
        "size",
        help="Measure artifact files and dependency closures, optionally using the shared CAS index",
        description="Read artifact inventories from tar or CAS, count shared graph nodes once, and report incomplete sizes explicitly.",
    )
    p.add_argument("targets", nargs="*", metavar="TARGET")
    p.add_argument("--scope", choices=("all", "run", "build"), default="run")
    p.add_argument(
        "--archive",
        nargs=2,
        action="append",
        default=[],
        metavar=("ELEMENT", "TAR"),
        help="Offline archive; repeatable, optionally mapped through --snapshot",
    )
    p.add_argument("--snapshot", help="Graph for offline archive inputs; no live project arguments allowed")
    p.add_argument(
        "--check-max",
        type=positive_int,
        metavar="BYTES",
        help="Exit 1 if complete total logical file bytes exceed this budget",
    )
    project_arguments(p)
    p.set_defaults(command="size")
    p.add_argument(
        "--cas-directory",
        default=os.environ.get("BST_CAS_DIRECTORY"),
        help="Enable shared bst-cas index using this local BuildStream cas directory (or BST_CAS_DIRECTORY)",
    )
    p.add_argument("--cas-index", help="Shared SQLite index path (or BST_CAS_INDEX); requires CAS mode")
    return p


def reachable(graph, root, adjacency=None):
    adjacency = graph.adjacency() if adjacency is None else adjacency
    seen = {root}
    pending = [root]
    while pending:
        for child, _ in adjacency[pending.pop()]:
            if child not in seen:
                seen.add(child)
                pending.append(child)
    return seen


def size_report(graph, manifests, errors=()):
    skipped = {n for n, v in graph.nodes.items() if v["kind"] in ("stack", "junction")}
    expected = set(graph.nodes) - skipped
    missing = sorted(expected - manifests.keys())

    def metrics(names):
        names = set(names) - skipped
        absent = sorted(names - manifests.keys())
        known = names & manifests.keys()
        content = {}
        count = 0
        total = 0
        for name in known:
            for entry in manifests[name].values():
                if entry["type"] in FILE_KINDS:
                    total += entry["size"]
                    count += 1
                    content[entry["sha256"]] = entry["size"]
        return {
            "file_bytes": None if absent else total,
            "known_file_bytes": total,
            "content_unique_bytes": None if absent else sum(content.values()),
            "known_content_unique_bytes": sum(content.values()),
            "regular_file_paths": count,
            "missing": absent,
        }

    adjacency = graph.adjacency()
    reachable_nodes = {name: reachable(graph, name, adjacency) for name in graph.nodes}
    closures = {root: reachable_nodes[root] for root in graph.targets}
    used = Counter(n for names in closures.values() for n in names)
    roots = [
        {"element": root, "closure": metrics(names), "exclusive": metrics(n for n in names if used[n] == 1)}
        for root, names in sorted(closures.items())
    ]
    rows = [
        {"element": name, "own": metrics([name]), "closure": metrics(reachable_nodes[name])}
        for name in sorted(graph.nodes)
    ]
    return {
        "format_version": 1,
        "query": "size",
        "total": metrics(graph.nodes),
        "roots": roots,
        "elements": rows,
        "coverage": {
            "complete": not missing and not errors,
            "missing": missing,
            "errors": list(errors),
            "skipped": sorted(skipped),
        },
        "notes": [
            "Logical file bytes count regular and hardlink paths; symlink bodies, directories and archive headers are excluded.",
            "Content-unique bytes deduplicate full-file SHA-256, not CAS chunks, compressed data or installed filesystem blocks.",
            "Closures follow the selected visible graph. Root-exclusive sizes exclude elements reachable from other requested roots.",
            "No staging winner, split-rule result, integration effect or reclaimable CAS disk space is inferred.",
        ],
    }


def render_size(report):
    def size(value):
        return "unknown" if value is None else str(value) + " B"

    lines = [
        "Logical artifact files: " + size(report["total"]["file_bytes"]),
        "Unique file content: " + size(report["total"]["content_unique_bytes"]),
    ]
    for root in report["roots"]:
        lines.append(
            root["element"]
            + ": closure "
            + size(root["closure"]["file_bytes"])
            + ", exclusive "
            + size(root["exclusive"]["file_bytes"])
        )
    lines += ["", "Element | own bytes | closure bytes"]
    for row in sorted(report["elements"], key=lambda r: (-(r["own"]["known_file_bytes"]), r["element"])):
        lines.append(
            row["element"] + " | " + size(row["own"]["file_bytes"]) + " | " + size(row["closure"]["file_bytes"])
        )
    if not report["coverage"]["complete"]:
        lines.append("INCOMPLETE: known file bytes " + str(report["total"]["known_file_bytes"]))
        lines.append("Missing: " + ", ".join(report["coverage"]["missing"]))
        for error in report["coverage"]["errors"]:
            lines.append("Error: " + str(error))
    lines.append(report["notes"][1])
    return "\n".join(lines)


def _run_size(args, cas):
    if args.archive and (args.targets or args.directory or args.option):
        raise ValueError("--archive cannot be combined with live project arguments")
    if args.snapshot and (not args.archive or args.targets or args.directory or args.option):
        raise ValueError("--snapshot requires offline --archive inputs only")
    archives = dict(args.archive)
    if len(archives) != len(args.archive):
        raise ValueError("Duplicate archive element")
    runner = CliRunner(args.timeout, args.output_limit)
    cancelled = threading.Event()
    try:
        if args.snapshot:
            graph = read_snapshot(args.snapshot)
        elif args.archive:
            graph = Graph(
                sorted(archives),
                {n: {"kind": "import", "key": None, "source_info": None, "workspace": False} for n in archives},
                {},
                {"offline": True},
            )
        else:
            if not args.targets:
                raise ValueError("size requires targets or --archive inputs")
            graph = capture(args.targets, args.directory, args.option, run=runner)
        unknown = set(archives) - graph.nodes.keys()
        if unknown:
            raise ValueError("Archive elements absent from selected graph: " + ", ".join(sorted(unknown)))
        graph = graph.scoped(args.scope)
        manifests = {}
        errors = []
        if cas is not None and not args.archive:
            from bst_cas import load_artifacts

            project = Project(args.directory, args.option, run=lambda argv: runner(argv).stdout)
            keys = {
                name: node["key"] for name, node in graph.nodes.items() if node["kind"] not in ("stack", "junction")
            }
            for name, entries, identity, error in load_artifacts(cas, project, keys, keys):
                if error:
                    errors.append({"element": name, "message": error})
                else:
                    manifests[name] = {
                        entry["path"].lstrip("/"): {
                            "type": entry["kind"],
                            "size": entry["size"],
                            "sha256": entry.get("digest", "").split("/")[0],
                        }
                        for entry in entries
                    }
        else:
            with tempfile.TemporaryDirectory(prefix="bst-tree-size-") as root:
                for index, (name, node) in enumerate(sorted(graph.nodes.items())):
                    if node["kind"] in ("stack", "junction"):
                        continue
                    path = Path(archives[name]) if name in archives else Path(root) / (str(index) + ".tar")
                    try:
                        if not args.archive:
                            runner(
                                command_base(args.directory, args.option)
                                + [
                                    "artifact",
                                    "checkout",
                                    "--deps",
                                    "none",
                                    "--no-integrate",
                                    "--tar",
                                    str(path),
                                    "--",
                                    name,
                                ]
                            )
                            key = runner(
                                command_base(args.directory, args.option)
                                + ["show", "--deps", "none", "--format", "%{full-key}", "--", name]
                            ).stdout.strip()
                            if key != node["key"]:
                                raise ValueError("Artifact/project key changed while exporting; retry")
                        elif name not in archives:
                            continue
                        manifests[name] = Archive(path, cancelled).scan().entries
                    except (OSError, ValueError, tarfile.TarError, EOFError) as error:
                        errors.append({"element": name, "message": str(error)})
                    finally:
                        if not args.archive:
                            path.unlink(missing_ok=True)
        report = size_report(graph, manifests, errors)
        report["scope"] = args.scope
        if cas is not None:
            report["coverage"]["cas_index"] = cas.stats()
        print(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
            if args.format == "json"
            else render_size(report)
        )
        if not report["coverage"]["complete"]:
            return 2
        return 1 if args.check_max is not None and report["total"]["file_bytes"] > args.check_max else 0
    finally:
        cancelled.set()
        runner.cancel()


def run_size(args):
    if args.cas_index and not args.cas_directory:
        raise ValueError("--cas-index requires --cas-directory or BST_CAS_DIRECTORY")
    if not args.cas_directory or args.archive:
        return _run_size(args, None)
    try:
        from bst_cas.integration import open_index
    except ImportError as error:
        raise ValueError("CAS mode requires bst-cas; install ./contrib/bst-cas in the same environment") from error
    with open_index(args) as cas:
        return _run_size(args, cas)
