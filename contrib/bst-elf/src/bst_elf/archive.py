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
"""ELF-specific projection of the shared safe archive inventory."""

import threading
from bst_tools_core.archive import Archive
from bst_tools_core.paths import normalize_path as path_name
from .elf import read_elf


def scan(path, owner, *, max_elf_size=512 * 1024 * 1024):
    archive = Archive(path, threading.Event()).scan()
    records, errors = [], []
    for name, source in archive.entries.items():
        path_name(name)
        kind = source["type"]
        entry = {
            "id": owner + "::/" + name,
            "owner": owner,
            "path": "/" + name,
            "kind": kind,
            "size": source.get("size", 0),
            "mode": source["mode"],
        }
        if kind in ("symlink", "hardlink"):
            entry["target"] = source["target"]
        if kind == "file" and source["elf"]:
            try:
                if source["size"] > max_elf_size:
                    raise ValueError("ELF exceeds --max-elf-size")
                with archive.stream(name) as stream:
                    entry.update(sha256=source["sha256"], elf=read_elf(stream))
            except ValueError as error:
                errors.append({"owner": owner, "path": entry["path"], "message": str(error)})
        records.append(entry)
    return records, errors
