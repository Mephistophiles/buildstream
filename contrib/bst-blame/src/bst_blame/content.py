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
"""Optional streaming artifact fingerprints and bounded text diffs; never extract links."""

import difflib
import hashlib
import tarfile

from .index import normalize_path
from .process import child, diagnostic_text

TEXT_LIMIT = 64 * 1024
DIFF_LIMIT = 200


def inspect_archive(stream, wanted, *, text=False):
    result = {}
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            # The archive is never extracted; absolute names and '..' still
            # indicate malformed output and must not be attributed to a file.
            if member.name.startswith("/"):
                raise ValueError("Unexpected absolute path in artifact archive")
            path = normalize_path(member.name)
            if path not in wanted:
                continue
            if path in result:
                raise ValueError("Duplicate path in artifact archive")
            expected = wanted[path]
            if (
                not member.isfile()
                or member.size != expected["size"]
                or bool(member.mode & 0o111) != expected["executable"]
            ):
                raise ValueError("Artifact changed while comparing; links and special files are not followed")
            digest = hashlib.sha256()
            content, total = bytearray(), 0
            with archive.extractfile(member) as source:
                while chunk := source.read(64 * 1024):
                    digest.update(chunk)
                    total += len(chunk)
                    if text and member.size <= TEXT_LIMIT:
                        content.extend(chunk)
            if total != member.size:
                raise ValueError("Truncated artifact file")
            result[path] = {"sha256": digest.hexdigest()}
            if text and member.size <= TEXT_LIMIT and b"\x00" not in content:
                try:
                    result[path]["_text"] = content.decode("utf-8")
                except UnicodeDecodeError:
                    pass
    if result.keys() != wanted.keys():
        raise ValueError("File disappeared from artifact while comparing")
    return result


def fingerprints(project, name, wanted, *, text=False):
    with child(project.checkout_command(name), binary=True) as (process, diagnostics):
        result = inspect_archive(process.stdout, wanted, text=text)
        # tar readers stop at their end marker. Drain the remaining block padding
        # before waiting, otherwise a full stdout pipe can deadlock bst's writer.
        while process.stdout.read(64 * 1024):
            pass
        process.wait()
        if process.returncode:
            raise ValueError(diagnostic_text(diagnostics) or f"bst checkout exited {process.returncode}")
    records = project.report([name], ("full-key",))
    expected_key = next(iter(wanted.values()))["artifact_key"]
    if len(records) != 1 or records[0]["full-key"].strip() != expected_key:
        raise ValueError("Artifact key changed while comparing; retry")
    return result


def compare(project, index, coverage, *, text=False):
    by_element = {}
    for path, owners in index.items():
        if len(owners) < 2:
            continue
        for owner in owners:
            if owner["kind"] == "file":
                by_element.setdefault(owner["element"], {})[path] = owner
    for name, wanted in sorted(by_element.items()):
        try:
            result = fingerprints(project, name, wanted, text=text)
            for path, record in result.items():
                wanted[path].update(record)
        except (OSError, ValueError, tarfile.TarError) as error:
            coverage["errors"].append({"element": name, "message": str(error)})
            coverage["complete"] = False


def describe(path, owners, *, compared=False, text=False):
    metadata = {(o["kind"], o["size"], o["executable"], o["link_target"]) for o in owners}
    kinds = {o["kind"] for o in owners}
    relation = (
        "single-owner" if len(owners) == 1 else ("shared-directory" if kinds == {"directory"} else "potential-overlap")
    )
    comparison = "not-compared"
    if len(owners) < 2 or kinds == {"directory"}:
        comparison = "not-applicable"
    elif len(kinds) > 1:
        comparison = "different-types"
    elif kinds == {"symlink"}:
        comparison = "identical" if len({o["link_target"] for o in owners}) == 1 else "different"
    elif compared:
        comparison = (
            "incomplete"
            if any("sha256" not in o for o in owners)
            else ("identical" if len({o["sha256"] for o in owners}) == 1 else "different")
        )
    diffs = []
    if text and comparison == "different" and kinds == {"file"}:
        baseline = owners[0]
        for other in owners[1:]:
            if other["sha256"] == baseline["sha256"]:
                continue
            if "_text" not in baseline or "_text" not in other:
                continue
            # Preserve final-newline differences using splitlines(keepends=True).
            lines = list(
                difflib.unified_diff(
                    baseline["_text"].splitlines(keepends=True),
                    other["_text"].splitlines(keepends=True),
                    fromfile=baseline["element"] + "::" + path,
                    tofile=other["element"] + "::" + path,
                )
            )
            # difflib does not add the conventional marker for missing final
            # newlines; keep that difference visible in terminal and JSON output.
            display = []
            for line in lines:
                display.append(line if line.endswith("\n") else line + "\n")
                if not line.endswith("\n"):
                    display.append("\\ No newline at end of file\n")
            diffs.append(
                {
                    "from": baseline["element"],
                    "to": other["element"],
                    "lines": display[:DIFF_LIMIT],
                    "truncated": len(display) > DIFF_LIMIT,
                }
            )
    public_owners = [{key: value for key, value in owner.items() if not key.startswith("_")} for owner in owners]
    return {
        "type": "path",
        "path": path,
        "relationship": relation,
        "metadata_equal": len(metadata) == 1,
        "content_comparison": comparison,
        "owners": public_owners,
        "diffs": diffs,
    }
