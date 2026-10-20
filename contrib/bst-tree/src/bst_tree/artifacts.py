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
"""Artifact manifests and on-demand analyses; archive links are never extracted."""

from contextlib import contextmanager
import codecs
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import threading

from .adapter import command_base
from .artifact_process import Cancelled, CommandRunner

CHUNK = 1024 * 1024
PREVIEW_LIMIT = 256 * 1024
FILE_KINDS = ("file", "hardlink")
ERROR_STATUSES = ("error", "unavailable", "timeout")
ABI_NOTE = (
    "No reported ABI change is not a completeness guarantee. Type comparisons depend on debug information; "
    "without it abidiff can only compare available ELF interfaces. Exit 4 requires review and does not prove compatibility."
)


def archive_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\x00" in name:
        raise ValueError(f"Unsafe archive path: {name!r}")
    normalized = path.as_posix()
    return "" if normalized == "." else normalized


def check_cancelled(cancelled):
    if cancelled.is_set():
        raise Cancelled("Artifact comparison cancelled")


def fingerprint(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


class Archive:
    def __init__(self, path, cancelled):
        self.path = Path(path).resolve()
        self.cancelled = cancelled
        self.entries = {}
        self.members = {}
        self.resolved = {}
        self.written = {}
        self.fingerprint = None

    def scan(self):
        before = fingerprint(self.path)
        try:
            with tarfile.open(self.path, "r:*") as bundle:
                for member in bundle:
                    check_cancelled(self.cancelled)
                    name = archive_name(member.name)
                    if not name:
                        if member.isdir():
                            continue
                        raise ValueError("Archive root must be a directory")
                    if name in self.entries:
                        raise ValueError(f"Duplicate archive path: {name}")
                    kind = (
                        "file"
                        if member.isfile()
                        else (
                            "directory"
                            if member.isdir()
                            else (
                                "symlink"
                                if member.issym()
                                else (
                                    "hardlink"
                                    if member.islnk()
                                    else (
                                        "fifo"
                                        if member.isfifo()
                                        else "char" if member.ischr() else "block" if member.isblk() else "other"
                                    )
                                )
                            )
                        )
                    )
                    entry = {"type": kind, "mode": member.mode & 0o7777, "uid": member.uid, "gid": member.gid}
                    if kind in ("symlink", "hardlink"):
                        entry["target"] = member.linkname
                    if kind in ("char", "block"):
                        entry["device"] = [member.devmajor, member.devminor]
                    if kind == "file":
                        digest, prefix = hashlib.sha256(), b""
                        with bundle.extractfile(member) as stream:
                            while chunk := stream.read(CHUNK):
                                check_cancelled(self.cancelled)
                                digest.update(chunk)
                                prefix = (prefix + chunk[:4])[:4]
                        entry.update(size=member.size, sha256=digest.hexdigest(), elf=prefix == b"\x7fELF")
                        self.resolved[name] = name
                    self.entries[name] = entry
                    self.members[name] = member
        except (tarfile.TarError, EOFError) as error:
            raise ValueError(f"Unable to read artifact archive {self.path.name}: {error}") from error
        for name in self.entries:
            for parent in PurePosixPath(name).parents:
                ancestor = self.entries.get(parent.as_posix())
                if ancestor and ancestor["type"] != "directory":
                    raise ValueError(f"Archive path {name} has a non-directory ancestor: {parent}")
        for name, entry in self.entries.items():
            if entry["type"] != "hardlink":
                continue
            current, chain, seen = name, [], set()
            while current not in self.resolved:
                if current in seen or current not in self.entries or self.entries[current]["type"] != "hardlink":
                    raise ValueError(f"Invalid hardlink chain: {name}")
                seen.add(current)
                chain.append(current)
                current = archive_name(self.entries[current]["target"])
            target = self.resolved[current]
            for link in chain:
                self.resolved[link] = target
                self.entries[link].update({k: self.entries[target][k] for k in ("size", "sha256", "elf")})
        if before != fingerprint(self.path):
            raise ValueError("Artifact archive changed while reading; retry with a stable copy")
        self.fingerprint = before
        return self

    def check(self):
        check_cancelled(self.cancelled)
        if self.fingerprint != fingerprint(self.path):
            raise ValueError("Artifact archive changed after comparison; reopen it")

    @contextmanager
    def stream(self, name):
        self.check()
        if name not in self.resolved:
            raise ValueError("Only regular files and resolved hardlinks can be read")
        with tarfile.open(self.path, "r:*") as bundle:
            with bundle.extractfile(self.members[self.resolved[name]]) as stream:
                yield stream
        self.check()

    def preview(self, name, limit=PREVIEW_LIMIT):
        with self.stream(name) as stream:
            content = stream.read(limit + 1)
        if b"\x00" in content:
            return None, len(content) > limit
        try:
            decoder = codecs.getincrementaldecoder("utf-8")()
            return decoder.decode(content[:limit], final=len(content) <= limit), len(content) > limit
        except UnicodeDecodeError:
            return None, len(content) > limit

    def materialize(self, name, root):
        self.check()
        if name in self.written:
            return self.written[name]
        destination = root.joinpath(*PurePosixPath(name).parts)
        if not destination.is_relative_to(root):
            raise ValueError("File is outside the private comparison directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            digest = hashlib.sha256()
            with (
                self.stream(name) as stream,
                os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output,
            ):
                while chunk := stream.read(CHUNK):
                    check_cancelled(self.cancelled)
                    output.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != self.entries[name]["sha256"]:
                raise ValueError("Artifact contents changed while materializing a file")
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        self.written[name] = destination
        return destination


def compare_entries(old, new, *, paths=(), show_all=False):
    selected = set(archive_name(p) for p in paths)
    unknown = selected - (old.keys() | new.keys())
    if unknown:
        raise ValueError("Paths absent from both artifacts: " + ", ".join(sorted(unknown)))
    records = []
    for name in sorted(selected or old.keys() | new.keys()):
        before, after = old.get(name), new.get(name)
        if before == after and not show_all:
            continue
        fields = []
        if before is not None and after is not None:
            fields = [k for k in ("type", "mode", "uid", "gid", "target", "device") if before.get(k) != after.get(k)]
            if before.get("sha256") != after.get("sha256"):
                fields.append("content")
        records.append(
            {
                "path": name,
                "change": (
                    "added"
                    if before is None
                    else "removed" if after is None else "modified" if before != after else "unchanged"
                ),
                "fields": fields,
                "old": before,
                "new": after,
            }
        )
    return records


class ArtifactComparison:
    def __init__(
        self,
        old,
        new,
        *,
        archives=False,
        directory=None,
        options=(),
        paths=(),
        show_all=False,
        timeout=120,
        report_limit=1024 * 1024,
        debug_old=(),
        debug_new=(),
        suppressions=(),
        runner=None,
    ):
        self.inputs = old, new
        self.archives_mode = archives
        self.base = command_base(directory, options)
        self.paths, self.show_all = paths, show_all
        self.timeout, self.report_limit = timeout, report_limit
        self.debug = tuple(debug_old), tuple(debug_new)
        self.suppressions = tuple(suppressions)
        self.cancelled = threading.Event()
        self.runner = runner or CommandRunner(self.cancelled)
        self.serial = threading.Lock()
        self.temporary = tempfile.TemporaryDirectory(prefix="bst-tree-diff-")
        self.root = Path(self.temporary.name)
        self.archives = []
        self.records = []
        self.analyses = {}
        self.loaded = False

    @contextmanager
    def operation(self):
        with self.serial:
            check_cancelled(self.cancelled)
            try:
                yield
            finally:
                if self.cancelled.is_set():
                    self.temporary.cleanup()

    def close(self):
        self.cancelled.set()
        self.runner.cancel()
        if self.serial.acquire(blocking=False):
            try:
                self.temporary.cleanup()
            finally:
                self.serial.release()

    def load(self, progress=lambda text: None):
        with self.operation():
            if self.loaded:
                return
            for index, value in enumerate(self.inputs):
                side = "before" if index == 0 else "after"
                if self.archives_mode:
                    archive = Path(value)
                else:
                    progress(f"Exporting {side} artifact (no build / no integration)…")
                    archive = self.root / (side + ".tar")
                    result = self.runner(
                        self.base
                        + [
                            "artifact",
                            "checkout",
                            "--deps",
                            "none",
                            "--no-integrate",
                            "--tar",
                            str(archive),
                            "--",
                            value,
                        ],
                        timeout=self.timeout,
                        limit=self.report_limit,
                    )
                    if result.timed_out or result.returncode:
                        raise ValueError(
                            f"Unable to export {side} artifact: "
                            + (
                                "checkout timed out"
                                if result.timed_out
                                else result.stderr.strip() or str(result.returncode)
                            )
                        )
                progress(f"Hashing {side} artifact files…")
                self.archives.append(Archive(archive, self.cancelled).scan())
            self.records = compare_entries(
                *(a.entries for a in self.archives), paths=self.paths, show_all=self.show_all or bool(self.paths)
            )
            self.loaded = True

    def pair(self, path, *, elf=False):
        if not self.loaded:
            return False
        entries = [a.entries.get(path, {}) for a in self.archives]
        return all(e.get("type") in FILE_KINDS and (not elf or e.get("elf")) for e in entries)

    def text_diff(self, path):
        with self.operation():
            texts, limited = [], False
            for archive in self.archives:
                entry = archive.entries.get(path)
                if entry is None:
                    texts.append("")
                elif entry["type"] not in FILE_KINDS:
                    return (
                        "Text preview is available only for regular files; links and special files are not followed."
                    )
                else:
                    text, truncated = archive.preview(path)
                    if text is None:
                        return "Binary file; use abidiff for ELF ABI or diffoscope for detailed comparison."
                    texts.append(text)
                    limited |= truncated

            def lines(text):
                return [
                    (
                        line[:-2] + " [CRLF]"
                        if line.endswith("\r\n")
                        else line[:-1] if line.endswith("\n") else line + " [no final newline]"
                    )
                    for line in text.splitlines(keepends=True)
                ]

            result = "\n".join(
                difflib.unified_diff(
                    lines(texts[0]),
                    lines(texts[1]),
                    fromfile="before/" + path,
                    tofile="after/" + path,
                    lineterm="",
                    n=3,
                )
            )
            if not result:
                result = "No text differences in the preview."
            if limited:
                result += (
                    f"\n\n[Partial preview: first {PREVIEW_LIMIT} bytes per file; hashes compare the full contents.]"
                )
            return result

    def report(self):
        summary = {
            c: sum(r["change"] == c for r in self.records) for c in ("added", "removed", "modified", "unchanged")
        }
        eligible = [r["path"] for r in self.records if r["change"] != "unchanged" and self.pair(r["path"], elf=True)]
        return {
            "format_version": 1,
            "comparison": "artifacts",
            "inputs": {"old": self.inputs[0], "new": self.inputs[1]},
            "summary": summary,
            "elf_pairs": eligible,
            "changes": self.records,
            "analyses": list(self.analyses.values()),
            "notes": [
                "Content hashes, types, modes, numeric owners and link targets are compared; timestamps and extended attributes are not.",
                "Only the selected artifacts are exported; dependencies and integration commands are excluded.",
            ],
        }

    def analyze(self, tool, path=None, *, html=None):
        with self.operation():
            if tool not in ("abidiff", "diffoscope"):
                raise ValueError("Unknown analyzer: " + tool)
            if not self.loaded:
                raise ValueError("Load artifacts before analyzing files")
            if path is not None:
                path = archive_name(path)
            if tool == "abidiff" and (path is None or not self.pair(path, elf=True)):
                raise ValueError("abidiff requires a regular ELF file on both sides")
            if path is not None and not self.pair(path):
                raise ValueError("Selected-file analysis requires a regular file on both sides")
            result = {
                "tool": tool,
                "path": path,
                "status": "unavailable",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "note": (
                    ABI_NOTE
                    if tool == "abidiff"
                    else "diffoscope detail depends on installed comparators; report limits apply."
                ),
            }
            executable = shutil.which(tool)
            if executable is None:
                result["stderr"] = f"{tool} is not installed or is not on PATH"
                self.analyses[tool, path] = result
                return result
            try:
                for archive in self.archives:
                    archive.check()
                if path is None:
                    inputs = [a.path for a in self.archives]
                else:
                    inputs = [
                        a.materialize(path, self.root / side) for a, side in zip(self.archives, ("before", "after"))
                    ]
                if tool == "abidiff":
                    args = [executable, "--no-default-suppression"]
                    for side, dirs in enumerate(self.debug, 1):
                        for directory in dirs:
                            args += [f"--debug-info-dir{side}", str(Path(directory).resolve())]
                    for suppression in self.suppressions:
                        args += ["--suppressions", str(Path(suppression).resolve())]
                else:
                    args = [
                        executable,
                        "--text",
                        "-",
                        "--text-color",
                        "never",
                        "--no-progress",
                        "--diff-context",
                        "3",
                        "--max-text-report-size",
                        str(self.report_limit),
                        "--max-report-size",
                        str(self.report_limit),
                    ]
                    if html:
                        args += ["--html", str(self.root / "diffoscope.html"), "--output-empty"]
                args += [str(p) for p in inputs]
                execution = self.runner(args, timeout=self.timeout, limit=self.report_limit)
                code = execution.returncode
                if execution.timed_out:
                    status = "timeout"
                elif tool == "abidiff":
                    status = (
                        "error"
                        if code not in (0, 4, 12)
                        else "incompatible" if code == 12 else "changed" if code == 4 else "no_reported_change"
                    )
                else:
                    status = "no_reported_change" if code == 0 else "changed" if code == 1 else "error"
                result.update(
                    status=status,
                    exit_code=code,
                    stdout=execution.stdout,
                    stderr=execution.stderr,
                    truncated=execution.truncated,
                    command=args,
                )
                if html and status not in ERROR_STATUSES:
                    destination = Path(html).absolute()
                    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                        temporary = Path(stream.name)
                        try:
                            with (self.root / "diffoscope.html").open("rb") as source:
                                shutil.copyfileobj(source, stream)
                            stream.flush()
                            os.fsync(stream.fileno())
                            check_cancelled(self.cancelled)
                            os.replace(temporary, destination)
                        finally:
                            temporary.unlink(missing_ok=True)
                    result["html_report"] = str(destination)
                replacements = [
                    (str(p), ("before/" if i == 0 else "after/") + (path or "artifact.tar"))
                    for i, p in enumerate(inputs)
                ]
                replacements.append((str(self.root / "diffoscope.html"), "<html-report>"))
                for field in ("stdout", "stderr"):
                    for actual, label in replacements:
                        result[field] = result[field].replace(actual, label)
                result["command"] = [
                    next((arg.replace(actual, label) for actual, label in replacements if actual in arg), arg)
                    for arg in args
                ]
            except Cancelled:
                raise
            except (OSError, ValueError, tarfile.TarError) as error:
                result.update(status="error", stderr=str(error))
            self.analyses[tool, path] = result
            return result


def has_artifact_changes(report):
    return any(report["summary"][c] for c in ("added", "removed", "modified")) or any(
        r["status"] in ("changed", "incompatible") for r in report["analyses"]
    )


def has_analysis_errors(report):
    return any(r["status"] in ERROR_STATUSES for r in report["analyses"])


def render_artifacts(report, *, color=False):
    summary = report["summary"]
    lines = [
        f"Artifact files: +{summary['added']} -{summary['removed']} ~{summary['modified']}; ELF pairs: {len(report['elf_pairs'])}"
    ]
    for record in report["changes"][:30]:
        mark = {"added": "+", "removed": "-", "modified": "~", "unchanged": " "}[record["change"]]
        label = f"{mark} {record['path']}" + (" [" + ", ".join(record["fields"]) + "]" if record["fields"] else "")
        code = {"+": "32", "-": "31", "~": "33"}.get(mark)
        lines.append(f"\033[{code}m{label}\033[0m" if color and code else label)
    if len(report["changes"]) > 30:
        lines.append(f"… {len(report['changes']) - 30} more; use --format json or --tui")
    for result in report["analyses"]:
        lines += ["", f"{result['tool']} · {result['path'] or 'whole artifact'}: {result['status']}"]
        output = (result["stdout"] + "\n" + result["stderr"]).strip().splitlines()
        lines += output[:60]
        if len(output) > 60 or result["truncated"]:
            lines.append("[Report abbreviated; use --format json, --tui or --diffoscope-html.]")
        lines.append(result["note"])
    if has_analysis_errors(report):
        lines.append("Requested analysis is incomplete; see analyzer errors above.")
    elif not has_artifact_changes(report):
        lines.append("No differences in the selected comparison.")
    return "\n".join(lines)
