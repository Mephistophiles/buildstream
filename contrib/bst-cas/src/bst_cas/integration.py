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
"""Small consumer adapters; no dependency on any of the standalone tools."""

from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile

from .index import Index, load_artifact
from .config import cas_directory


def open_index(args):
    return Index(cas_directory(args.cas_directory), args.cas_index)


def elf_entries(index, entries, owner, read_elf, namespace, max_elf_size):
    records, errors = [], []
    for source in entries:
        entry = {
            "id": owner + "::" + source["path"],
            "owner": owner,
            "path": source["path"],
            "kind": source["kind"],
            "size": source["size"],
            "mode": None,
            "executable": source["executable"],
        }
        if source["kind"] == "symlink":
            entry["target"] = source["link_target"]
        if source["kind"] == "file":
            try:
                # Oversized non-ELF files do not need to be read in full just
                # to reject an ELF. Do not cache an incomplete inspection.
                if source["size"] > max_elf_size:
                    with index._open(source["digest"]) as stream:
                        if stream.read(4) == b"\x7fELF":
                            raise ValueError("ELF exceeds --max-elf-size")
                else:

                    def analyze(stream):
                        return read_elf(stream) if stream.read(4) == b"\x7fELF" else None

                    elf = index.analyze(source["digest"], namespace, analyze)
                    if elf is not None:
                        entry.update(elf=elf, sha256=source["digest"].split("/")[0])
            except (OSError, ValueError) as error:
                errors.append({"owner": owner, "path": source["path"], "message": str(error)})
        records.append(entry)
    return records, errors


@contextmanager
def artifact_files(index, project, name, *, loaded=None):
    """Private copies for ripgrep; omit target links, never link CAS into a tree."""
    entries, identity = loaded if loaded is not None else load_artifact(index, project, name)
    with tempfile.TemporaryDirectory(prefix="bst-cas-search-") as temporary:
        root = Path(temporary)
        for entry in entries:
            if entry["kind"] != "file":
                continue
            destination = root / entry["path"].lstrip("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with index.blob(entry["digest"]) as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
        yield root, "cas:" + identity["artifact-cas-digest"]
