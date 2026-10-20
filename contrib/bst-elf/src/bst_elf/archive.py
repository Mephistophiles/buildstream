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
"""Read archives without extracting files or following host filesystem links."""

import hashlib
from pathlib import PurePosixPath
import tarfile

from .elf import read_elf


def path_name(value):
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("Empty path or unsupported path characters")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ValueError("Archive paths must not contain '..'")
    return "/" + str(path).lstrip("/") if str(path) != "." else "/"


def scan(path, owner, *, max_elf_size=512 * 1024 * 1024):
    entries, errors = {}, []
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            if member.name.startswith("/"):
                raise ValueError("Absolute archive member path")
            name = path_name(member.name)
            if name == "/" and member.isdir():
                continue
            if name in entries:
                raise ValueError("Duplicate archive path: " + name)
            kind = (
                "file"
                if member.isfile()
                else (
                    "directory"
                    if member.isdir()
                    else "symlink" if member.issym() else "hardlink" if member.islnk() else "special"
                )
            )
            entry = {
                "id": owner + "::" + name,
                "owner": owner,
                "path": name,
                "kind": kind,
                "size": member.size,
                "mode": member.mode & 0o7777,
            }
            if kind in ("symlink", "hardlink"):
                entry["target"] = member.linkname
            if kind == "file":
                with archive.extractfile(member) as stream:
                    if stream.read(4) == b"\x7fELF":
                        try:
                            if member.size > max_elf_size:
                                raise ValueError("ELF exceeds --max-elf-size")
                            digest = hashlib.sha256()
                            stream.seek(0)
                            while chunk := stream.read(1024 * 1024):
                                digest.update(chunk)
                            entry["sha256"] = digest.hexdigest()
                            entry["elf"] = read_elf(stream)
                        except ValueError as error:
                            errors.append({"owner": owner, "path": name, "message": str(error)})
            entries[name] = entry
    for name in entries:
        for parent in PurePosixPath(name).parents:
            ancestor = entries.get(str(parent))
            if ancestor and ancestor["kind"] != "directory":
                raise ValueError("Non-directory archive ancestor: " + str(parent))
    return list(entries.values()), errors
