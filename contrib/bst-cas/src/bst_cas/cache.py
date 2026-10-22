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
"""Index files trees referenced by local BuildStream 2 artifact metadata."""

import os
from pathlib import Path
import stat

from google.protobuf.message import DecodeError

from .index import digest_parts, MAX_DIRECTORY
from .reapi import Artifact, Source


def index_cache(index, refs_directory=None, progress=None, *, sources=False, on_tree=None, seen=None, on_metadata=None):
    refs = Path(refs_directory) if refs_directory else index.cas_directory.parent / "artifacts" / "refs"
    result = {"cas_directory": str(index.cas_directory), "refs_directory": str(refs),
              "index_path": str(index.database), "refs": 0, "indexed_trees": 0,
              "reused_refs": 0, "without_files": 0, "errors": [], "complete": False}
    seen = set() if seen is None else seen

    def error(path, message):
        result["errors"].append({"ref": str(path), "message": str(message)})

    def walk_error(exception):
        error(exception.filename, exception)

    # Descriptor-relative reads avoid following metadata symlinks or opening
    # special files. Tree validation and checksum verification live in Index.
    root_fd = os.open(refs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for directory, children, files, directory_fd in os.fwalk(
                ".", follow_symlinks=False, dir_fd=root_fd, onerror=walk_error):
            for name in list(children):
                try:
                    if stat.S_ISLNK(os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode):
                        children.remove(name)
                        error(Path(directory) / name, "Symlinked metadata directory is unsupported")
                except OSError as exception:
                    children.remove(name)
                    error(Path(directory) / name, exception)
            for name in sorted(files):
                ref = str(Path(directory) / name)
                result["refs"] += 1
                try:
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                    with os.fdopen(fd, "rb") as stream:
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DIRECTORY:
                            raise ValueError("Artifact metadata must be a regular file at most 16 MiB")
                        data = stream.read(MAX_DIRECTORY + 1)
                        if len(data) > MAX_DIRECTORY:
                            raise ValueError("Artifact metadata exceeds 16 MiB")
                    artifact = (Source if sources else Artifact).FromString(data)
                    if artifact.version not in ((0,) if sources else (2,)):
                        raise ValueError(f"Unsupported artifact metadata version: {artifact.version}")
                    if on_metadata is not None:
                        on_metadata(ref, artifact)
                        continue
                    if not artifact.HasField("files"):
                        if sources:
                            raise ValueError("Source metadata has no files tree")
                        result["without_files"] += 1
                        continue
                    digest = f"{artifact.files.hash}/{artifact.files.size_bytes}"
                    digest_parts(digest)
                    if digest in seen:
                        result["reused_refs"] += 1
                        continue
                    entries = index.tree(digest)
                    if on_tree:
                        on_tree(str(refs / ref), digest, entries)
                    seen.add(digest)
                    result["indexed_trees"] += 1
                except BrokenPipeError:
                    raise
                except (OSError, ValueError, DecodeError) as exception:
                    error(ref, exception)
                finally:
                    if progress:
                        progress(result)
    finally:
        os.close(root_fd)
    result["complete"] = not result["errors"]
    result["stats"] = index.stats()
    return result


def source_owners(index):
    """Map exact saved source digests to historical project/normalized-element IDs."""
    refs = index.cas_directory.parent / "artifacts" / "refs"
    owners = {}
    if not refs.exists():
        return owners, []

    def record(ref, artifact):
        parts = Path(ref).parts
        if len(parts) != 3:
            raise ValueError("Expected project/element/key artifact reference")
        if not artifact.HasField("sources"):
            return
        digest = f"{artifact.sources.hash}/{artifact.sources.size_bytes}"
        digest_parts(digest)
        owner = "/".join(parts[:2])
        owners.setdefault(digest, {}).setdefault(owner, set()).add(ref)

    report = index_cache(index, refs, on_metadata=record)
    return {
        digest: [{"element": owner, "artifact_refs": sorted(references)}
                 for owner, references in sorted(values.items())]
        for digest, values in owners.items()
    }, report["errors"]
