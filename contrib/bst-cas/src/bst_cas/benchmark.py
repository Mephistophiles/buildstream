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
"""Reproducible single-owner versus batched CAS reads on a real project."""

import argparse
import json
import os
from pathlib import Path
import tempfile
from time import perf_counter

from . import Index, load_artifacts


def measure(project, cas_directory, keys):
    rows, baseline = [], None
    with tempfile.TemporaryDirectory(prefix="bst-cas-benchmark-") as temporary:
        for mode in ("single-owner", "batched"):
            with Index(cas_directory, Path(temporary) / (mode + ".sqlite3")) as index:
                for cache in ("cold-index", "warm-index"):
                    before = index.stats()
                    started = perf_counter()
                    results = {}
                    groups = ([name] for name in sorted(keys)) if mode == "single-owner" else [keys]
                    for names in groups:
                        for name, entries, identity, error in load_artifacts(index, project, names, keys):
                            if error:
                                raise ValueError(name + ": " + error)
                            results[name] = (identity, len(entries))
                    elapsed = perf_counter() - started
                    if baseline is None:
                        baseline = results
                    if baseline != results:
                        raise ValueError("Artifact identities or inventories changed between benchmark runs")
                    after = index.stats()
                    rows.append(
                        {
                            "mode": mode,
                            "cache": cache,
                            "seconds": elapsed,
                            **{
                                key: after[key] - before[key]
                                for key in ("identity_queries", "shared_tree_reuses", "hits", "misses")
                            },
                        }
                    )
    return {
        "format_version": 1,
        "query": "cas-benchmark",
        "artifacts": len(keys),
        "equivalent": True,
        "measurements": rows,
        "notes": [
            "Cold/warm refer to the derived SQLite index; OS and BuildStream caches are not flushed.",
            "Single-owner mode reproduces per-artifact identity queries; both modes verify identities and local CAS availability.",
            "No builds, artifact pulls or CAS writes are performed. Timings include bst startup and project loading.",
        ],
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("targets", nargs="+")
    p.add_argument("-C", "--directory")
    p.add_argument("--option", nargs=2, action="append", default=[])
    p.add_argument("--deps", choices=("none", "run", "build", "all"), default="run")
    p.add_argument("--cas-directory", default=os.environ.get("BST_CAS_DIRECTORY"))
    args = p.parse_intermixed_args(argv)
    try:
        from bst_tools_core.project import Project

        project = Project(args.directory, args.option)
        graph = project.graph(args.targets, args.deps)
        nodes = {
            name: graph.nodes[name]
            for name in graph.selected
            if graph.nodes[name]["kind"] not in ("stack", "junction")
        }
        if not nodes or any(node["state"] not in ("cached", "failed") for node in nodes.values()):
            raise ValueError("Benchmark requires a nonempty selection of locally cached artifacts")
        print(json.dumps(measure(project, args.cas_directory, {n: v["full-key"] for n, v in nodes.items()}), indent=2))
        return 0
    except KeyboardInterrupt:
        return 130
    except (ImportError, OSError, ValueError) as error:
        print(json.dumps({"format_version": 1, "complete": False, "error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
