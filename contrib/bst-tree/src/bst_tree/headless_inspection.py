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
"""Scriptable element inspection, with explicit source checkout and artifact pull."""

from dataclasses import asdict
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .headless import HeadlessRunner, project_arguments
from .artifact_cli import positive_int
from .inspection import ProjectInspector, preview_file
from .model import DETAIL_FIELDS, validate_details


def configure_parser(commands):
    sub = commands.add_parser(
        "inspect",
        help="Inspect effective configuration, sources or artifact files without a TUI",
        description="Read one live element through public BuildStream commands. Source checkout and artifact pull are opt-in.",
        epilog="Examples: bst-tree inspect app.bst -C /project --format json; "
        "bst-tree inspect app.bst --section artifacts --path usr/share/app.conf; "
        "bst-tree inspect app.bst --section sources --checkout --files.",
    )
    sub.add_argument("element", help="Element name, including junction-qualified names")
    sub.add_argument(
        "--section",
        choices=("build", "sources", "artifacts"),
        default="build",
        help="Inspection section (default: build)",
    )
    sub.add_argument(
        "--path",
        metavar="PATH",
        help="Preview an exact relative regular-file path in sources or artifacts; links are not followed",
    )
    sub.add_argument(
        "--files",
        action="store_true",
        help="List source files, including hidden files (requires workspace or --checkout)",
    )
    sub.add_argument(
        "--checkout",
        action="store_true",
        help="Sources only: create a temporary source checkout including build scripts; may fetch sources; removed on exit",
    )
    sub.add_argument(
        "--pull",
        action="store_true",
        help="Artifacts only: explicitly pull this element without dependencies, then inspect it",
    )
    sub.add_argument(
        "--limit",
        type=positive_int,
        default=256 * 1024,
        metavar="BYTES",
        help="Maximum bytes read for a text preview (default: 256 KiB)",
    )
    project_arguments(sub)


def yaml_value(text, expected, field):
    try:
        value = YAML(typ="safe").load(text)
    except YAMLError as error:
        raise ValueError(f"Malformed YAML in {field}: {error}") from error
    if value is None:
        value = expected()
    if not isinstance(value, expected):
        raise ValueError(f"Unexpected {field} data from BuildStream")
    return value


def workspace(inspector, element):
    value = inspector.show(element, "%{workspace-dirs}").rstrip("\n")
    if not value:
        return None
    if not value.startswith("Workspace: "):
        raise ValueError("Unexpected workspace-dirs output from BuildStream")
    path = Path(value[len("Workspace: ") :])
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f"Workspace directory is unavailable: {path}")
    return path.resolve()


def source_path(root, value):
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\x00" in value:
        raise ValueError("Source preview requires a relative file path inside the source tree")
    selected = root
    for part in path.parts:
        selected /= part
        if selected.is_symlink():
            raise ValueError("Source symlinks are not followed")
    return selected


def source_files(root):
    entries = []
    pending = [root]
    while pending:
        parent = pending.pop()
        with os.scandir(parent) as children:
            for entry in sorted(children, key=lambda e: e.name):
                info = entry.stat(follow_symlinks=False)
                kind = (
                    "directory"
                    if stat.S_ISDIR(info.st_mode)
                    else (
                        "file"
                        if stat.S_ISREG(info.st_mode)
                        else "symlink" if stat.S_ISLNK(info.st_mode) else "special"
                    )
                )
                item = {
                    "path": Path(entry.path).relative_to(root).as_posix(),
                    "type": kind,
                    "mode": stat.S_IMODE(info.st_mode),
                    "size": info.st_size,
                }
                if kind == "symlink":
                    item["target"] = os.readlink(entry.path)
                if kind == "directory":
                    pending.append(Path(entry.path))
                entries.append(item)
    return sorted(entries, key=lambda item: item["path"])


def inspect_element(args, inspector):
    report = {"format_version": 1, "query": "inspect", "element": args.element, "section": args.section}
    if args.section == "build":
        details = {
            field: yaml_value(inspector.show(args.element, "%{" + field + "}"), dict, field) for field in DETAIL_FIELDS
        }
        validate_details({args.element: details}, {args.element: {}})
        report["details"] = details
    elif args.section == "artifacts":
        result = inspector.pull_artifact(args.element) if args.pull else inspector.load(args.element, "artifacts")
        report["pulled"] = args.pull
        report["files"] = [asdict(entry) for entry in result.artifacts]
        if args.path is not None:
            report["preview"] = {
                "path": args.path,
                **inspector.preview_artifact(args.element, args.path, args.limit, structured=True),
            }
    else:
        report["provenance"] = yaml_value(inspector.show(args.element, "%{source-info}"), list, "source-info")
        original = workspace(inspector, args.element)
        root = inspector.checkout_sources(args.element).workspace if args.checkout else original
        report.update(
            workspace=str(original) if original else None,
            origin="checkout" if args.checkout else "workspace" if root else "unavailable",
            can_checkout=True,
            checkout_removed_on_exit=bool(args.checkout),
        )
        if (args.files or args.path is not None) and root is None:
            raise ValueError("No open source workspace; add --checkout to inspect a temporary source checkout")
        if args.files:
            report["files"] = source_files(root)
        if args.path is not None:
            report["preview"] = {
                "path": args.path,
                **preview_file(root, source_path(root, args.path), args.limit, structured=True),
            }
    try:
        json.dumps(report, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise ValueError("BuildStream inspection returned non-JSON-compatible data") from error
    return report


def render_inspection(report):
    lines = [f"{report['element']} · {report['section']}"]
    if "details" in report:
        for field, value in report["details"].items():
            lines += ["", field + ":", json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)]
    if "provenance" in report:
        lines += [
            "",
            "Source provenance:",
            json.dumps(report["provenance"], ensure_ascii=False, indent=2, sort_keys=True),
            "Workspace: " + (report["workspace"] or "none"),
        ]
        if report["origin"] == "checkout":
            lines.append("Temporary source checkout (removed after inspection; build scripts included, not executed).")
        elif report["origin"] == "unavailable":
            lines.append("Add --checkout --files to inspect source files without a workspace.")
    if report.get("pulled"):
        lines.append("Artifact pull completed (without dependencies).")
    if "files" in report:
        lines += ["", "Files:"]
        if not report["files"]:
            lines.append("(empty)")
        for item in report["files"]:
            if "details" in item:
                lines.append(item["path"] + "  " + item["details"])
            else:
                lines.append(
                    f"{item['path']} [{item['type']}, {item['mode']:04o}]"
                    + (" -> " + item["target"] if "target" in item else "")
                )
    if "preview" in report:
        preview = report["preview"]
        lines += [
            "",
            preview["path"] + ":",
            preview["text"] if not preview["binary"] else "Binary file; text preview unavailable.",
        ]
        if preview["truncated"]:
            lines.append(f"[Preview truncated at {preview['limit_bytes']} bytes]")
    return "\n".join(lines)


def run_inspection(args):
    if args.path is not None:
        path = PurePosixPath(args.path)
        if path.is_absolute() or not path.parts or ".." in path.parts or "\x00" in args.path:
            raise ValueError("--path requires a relative file path inside the selected tree")
    if args.section == "build" and (args.path is not None or args.files):
        raise ValueError("--path and --files apply to sources or artifacts, not build configuration")
    if args.checkout and args.section != "sources":
        raise ValueError("--checkout requires --section sources")
    if args.pull and args.section != "artifacts":
        raise ValueError("--pull requires --section artifacts")
    if args.files and args.section != "sources":
        raise ValueError("--files requires --section sources; artifact files are listed automatically")
    runner = HeadlessRunner(args.timeout, args.output_limit)
    inspector = ProjectInspector(args.directory, args.option, run=runner, cancel=runner.cancel)
    try:
        report = inspect_element(args, inspector)
        print(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
            if args.format == "json"
            else render_inspection(report)
        )
        return 0
    except tarfile.TarError as error:
        raise ValueError(f"Unable to read artifact preview: {error}") from error
    finally:
        inspector.cancel()
