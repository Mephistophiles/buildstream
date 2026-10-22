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
"""Standalone artifact-aware ELF inspector, with explicit target-root search rules."""

import argparse
import json
import os
import re
from pathlib import Path
import sys
import tarfile
import tempfile

from ruamel.yaml.error import YAMLError

from .archive import path_name, scan
from .elf import read_elf
from bst_tools_core.cli import positive_int as positive, positive_float as seconds
from bst_tools_core.process import CommandRunner
from bst_tools_core.project import Project
from .resolve import Resolver, load_profile


def parser():
    p = argparse.ArgumentParser(
        prog="bst-elf",
        description="Read ELF dependencies and BuildStream owners without executing target code.",
        epilog="Example: bst-elf product.bst --path /usr/bin/app --json --check. "
        "Checkout may retrieve missing data; no builds or integration commands run. "
        "Resolution uses an explicit target-root model, not the host dynamic loader.",
    )
    p.add_argument("targets", nargs="*", metavar="TARGET")
    p.add_argument("-C", "--directory", help="BuildStream project directory")
    p.add_argument("--option", nargs=2, action="append", default=[], metavar=("NAME", "VALUE"))
    p.add_argument(
        "--deps", choices=("none", "run", "build", "all"), default="run", help="Selected artifact scope (default: run)"
    )
    p.add_argument(
        "--archive",
        nargs=2,
        action="append",
        default=[],
        metavar=("OWNER", "TAR"),
        help="Offline archive input; repeatable, replaces live targets",
    )
    p.add_argument(
        "--path",
        action="append",
        default=[],
        help="ELF root path in the target filesystem; recurse through resolved NEEDED entries",
    )
    p.add_argument("--profile", help="JSON search profile; default directories /lib and /usr/lib")
    p.add_argument(
        "--library-path",
        action="append",
        default=[],
        metavar="DIR",
        help="Explicit target-root library directory; repeatable, no host environment lookup",
    )
    p.add_argument(
        "--expect-machine", metavar="EM_NAME", help="Report unexpected ELF e_machine values, e.g. EM_AARCH64"
    )
    p.add_argument("--max-elf-size", type=positive, default=512 * 1024 * 1024, metavar="BYTES")
    p.add_argument("--max-contexts", type=positive, default=10000)
    p.add_argument(
        "--timeout", type=seconds, default=120, metavar="SECONDS", help="Timeout per bst invocation (default: 120)"
    )
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--json", dest="format", action="store_const", const="json")
    p.add_argument("--check", action="store_true", help="Exit 1 for findings; incomplete/error always exits 2")
    p.add_argument(
        "--cas-directory",
        default=os.environ.get("BST_CAS_DIRECTORY"),
        help="Enable shared bst-cas index using this local BuildStream cas directory (or BST_CAS_DIRECTORY)",
    )
    p.add_argument("--cas-index", help="Shared SQLite index path (or BST_CAS_INDEX); requires CAS mode")
    return p


def project_library_path(project, targets):
    """Read resolved target environments, never the invoking host environment."""
    paths = []
    for record in project.report(targets, ("name", "env"), "none"):
        try:
            environment = project.yaml.load(record["env"])
        except YAMLError as error:
            raise ValueError("Malformed environment from bst show") from error
        if not isinstance(environment, dict):
            raise ValueError("Invalid environment from bst show")
        value = environment.get("LD_LIBRARY_PATH", "")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("LD_LIBRARY_PATH must be a string without NUL")
        # An unset/empty variable adds no directories. Embedded empty entries
        # retain their cwd semantics and are validated by Resolver.expand().
        directories = re.split(r"[:;]", value) if value else []
        paths.append(directories)
    if not paths:
        raise ValueError("No target environment from bst show")
    if any(value != paths[0] for value in paths[1:]):
        raise ValueError("Targets have different LD_LIBRARY_PATH values; inspect them separately")
    return paths[0]


def collect(args, runner, cas=None, *, profile=None):
    entries = []
    coverage = {"selected": 0, "inspected": 0, "missing": [], "skipped": [], "errors": [], "complete": False}
    if args.archive and (args.targets or args.directory or args.option):
        raise ValueError("--archive cannot be combined with live project arguments")
    if not args.archive and not args.targets:
        raise ValueError("Specify live targets or --archive OWNER TAR")
    if len({owner for owner, _ in args.archive}) != len(args.archive):
        raise ValueError("Duplicate archive owner")

    def execute(argv):
        result = runner(argv, timeout=args.timeout, limit=32 * 1024 * 1024)
        if result.returncode or result.timed_out or result.truncated:
            raise ValueError(result.stderr.strip() or "BuildStream command failed, timed out or exceeded output limit")
        return result.stdout

    project = None
    graph = None
    if args.archive:
        inputs = args.archive
        coverage["selected"] = len(inputs)
    else:
        project = Project(args.directory, args.option, run=execute)
        graph = project.graph(args.targets, args.deps)
        if profile is not None:
            profile["library_path"] += project_library_path(project, args.targets)
        inputs = []
        coverage["selected"] = len(graph.selected)
        for name in sorted(graph.selected):
            node = graph.nodes[name]
            if node["kind"] in ("stack", "junction"):
                coverage["skipped"].append(name)
            elif node["state"] not in ("cached", "failed"):
                coverage["missing"].append({"owner": name, "state": node["state"]})
            else:
                inputs.append((name, None))
    prepared = ((name, None, None, None) for name, _ in inputs)
    if project and cas is not None:
        from bst_cas import load_artifacts

        prepared = load_artifacts(
            cas, project, [name for name, _ in inputs], {name: graph.nodes[name]["full-key"] for name, _ in inputs}
        )
    archives = dict(inputs)
    with tempfile.TemporaryDirectory(prefix="bst-elf-") as directory:
        for index, (owner, manifest, identity, problem) in enumerate(prepared):
            archive = archives[owner]
            try:
                if problem:
                    raise ValueError(problem)
                if project and cas is not None:
                    from bst_cas.integration import elf_entries
                    import elftools

                    before = [identity]
                    records, errors = elf_entries(
                        cas,
                        manifest,
                        owner,
                        read_elf,
                        "bst-elf-v1:pyelftools-" + elftools.__version__,
                        args.max_elf_size,
                    )
                elif project:
                    archive = Path(directory) / (str(index) + ".tar")
                    before = project.report([owner], ("full-key", "artifact-cas-digest"))
                    if len(before) != 1:
                        raise ValueError("Artifact disappeared before export")
                    with archive.open("wb") as output:
                        result = runner(
                            project.checkout_command(owner),
                            timeout=args.timeout,
                            limit=1024 * 1024,
                            stdout_file=output,
                        )
                    if result.returncode or result.timed_out or result.truncated:
                        raise ValueError(
                            result.stderr.strip()
                            or "Artifact checkout failed, timed out or diagnostics exceeded limit"
                        )
                    after = project.report([owner], ("full-key", "artifact-cas-digest"))
                    if after != before:
                        raise ValueError("Artifact identity changed during export; retry")
                    if before[0]["full-key"].strip() != graph.nodes[owner]["full-key"]:
                        raise ValueError("Project key changed during scan; retry")
                if cas is None or not project:
                    records, errors = scan(archive, owner, max_elf_size=args.max_elf_size)
                if graph:
                    for record in records:
                        record.update(
                            artifact_key=before[0]["full-key"].strip(),
                            artifact_digest=before[0]["artifact-cas-digest"].strip(),
                            dependency_paths=graph.paths(owner),
                        )
                entries.extend(records)
                coverage["errors"].extend(errors)
                coverage["inspected"] += 1
            except (OSError, ValueError, tarfile.TarError, EOFError) as error:
                coverage["errors"].append({"owner": owner, "message": str(error)})
            finally:
                if project and archive:
                    archive.unlink(missing_ok=True)
    coverage["complete"] = not coverage["missing"] and not coverage["errors"]
    return entries, coverage


def safe(value):
    return "".join(c if c.isprintable() else "\\x%02x" % ord(c) for c in str(value))


def render(report):
    lines = [
        f"ELF files: {len(report['files'])}; findings: {len(report['findings'])}; coverage: {'complete' if report['coverage']['complete'] else 'INCOMPLETE'}"
    ]
    files = {f["id"]: f for f in report["files"]}
    shown = set()
    for context in report["contexts"]:
        entry = files[context["file"]]
        key = (entry["id"], tuple(context["inherited_rpath"]))
        if key in shown:
            continue
        shown.add(key)
        elf = entry["elf"]
        lines += [
            "",
            safe(entry["id"]) + f" [{elf['machine']}, ELF{elf['class']}, {elf['type']}]",
            "  SONAME: " + safe(elf["soname"] or "-"),
            "  RPATH: " + safe(elf["rpath"]),
            "  RUNPATH: " + safe(elf["runpath"]),
        ]
        for dependency in context["dependencies"]:
            label = "  " + safe(dependency["kind"]) + " " + safe(dependency["needed"]) + ": " + dependency["status"]
            if dependency["candidates"]:
                label += " -> " + ", ".join(safe(c["id"]) for c in dependency["candidates"])
            lines.append(label)
            if dependency["status"] != "resolved":
                lines.append("    searched: " + safe(", ".join(dependency["searched"])))
    for finding in report["findings"]:
        if finding.get("reason") == "unexpected-machine":
            lines.append("Unexpected architecture: " + safe(finding["file"]))
    for error in report["coverage"]["errors"]:
        lines.append("Error: " + safe(error))
    for missing in report["coverage"]["missing"]:
        lines.append("Not inspected: " + safe(missing))
    lines.append(
        "Resolution is an explicit target-root model; runtime dlopen, symbol binding, loader caches, hwcaps and integration effects are not modeled."
    )
    return "\n".join(lines)


def main(argv=None):
    args = parser().parse_intermixed_args(argv)
    runner = CommandRunner()
    cas = None
    try:
        if args.cas_index and not args.cas_directory:
            raise ValueError("--cas-index requires --cas-directory or BST_CAS_DIRECTORY")
        if args.cas_directory and not args.archive:
            try:
                from bst_cas.integration import open_index
            except ImportError as error:
                raise ValueError(
                    "CAS mode requires bst-cas; install ./contrib/bst-cas in the same environment"
                ) from error
            cas = open_index(args)
        profile = load_profile(args.profile, args.library_path)
        entries, coverage = collect(args, runner, cas, profile=profile)
        if cas is not None:
            coverage["cas_index"] = cas.stats()
        try:
            contexts, findings, errors = Resolver(entries, profile).audit(
                [path_name(p) for p in args.path], args.expect_machine, args.max_contexts
            )
        except ValueError as error:
            contexts, findings, errors = [], [], [{"message": str(error)}]
        coverage["errors"].extend(errors)
        coverage["complete"] &= not errors
        report = {
            "format_version": 1,
            "query": "elf-dependencies",
            "profile": profile,
            "files": sorted([e for e in entries if "elf" in e], key=lambda e: e["id"]),
            "contexts": contexts,
            "findings": findings,
            "coverage": coverage,
            "limitations": [
                "Declared DT_NEEDED/interpreter analysis only; no target code executed.",
                "No host library lookup, ld.so.cache, hwcaps, LD_PRELOAD, secure-execution simulation, dlopen or symbol binding.",
                "Artifact overlaps remain ambiguous; no staging winner or post-integration filesystem is inferred.",
            ],
        }
        print(
            json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True)
            if args.format == "json"
            else render(report)
        )
        return 2 if not coverage["complete"] else 1 if not report["files"] or (args.check and findings) else 0
    except KeyboardInterrupt:
        if args.format == "json":
            print(json.dumps({"format_version": 1, "error": "Interrupted", "complete": False}))
        else:
            print("bst-elf: interrupted", file=sys.stderr)
        return 130
    except (OSError, ValueError, tarfile.TarError) as error:
        if args.format == "json":
            print(json.dumps({"format_version": 1, "error": str(error), "complete": False}))
        else:
            print("bst-elf: " + safe(error), file=sys.stderr)
        return 2
    finally:
        runner.cancel()
        if cas is not None:
            cas.close()


if __name__ == "__main__":
    sys.exit(main())
