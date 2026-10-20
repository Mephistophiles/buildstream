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
"""Safe archive inventories, verified reads and private regular-file copies."""

from contextlib import contextmanager
import codecs
import hashlib
import os
from pathlib import Path, PurePosixPath
import tarfile
from .process import Cancelled

CHUNK = 1024 * 1024
PREVIEW_LIMIT = 256 * 1024
FILE_KINDS = ("file", "hardlink")


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
