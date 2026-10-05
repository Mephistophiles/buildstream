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
"""On-demand element inspection through public BuildStream commands."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import stat
import shlex
import tempfile
import threading
import subprocess
import tarfile

from .adapter import command_base


@dataclass
class ArtifactEntry:
    path: str
    details: str
    directory: bool = False


@dataclass
class Inspection:
    text: str
    workspace: Path | None = None
    can_checkout: bool = False
    artifacts: list[ArtifactEntry] | None = None


class ProjectInspector:
    def __init__(self, directory=None, options=(), *, run=subprocess.run, cancel=None):
        self.base = command_base(directory, options)
        self.run = run
        self._cancel = cancel or (lambda: None)
        self._lock = threading.Lock()
        self._cancelled = False
        self._checkout = None
        self._artifact_previews = {}
        self._preview_lock = threading.Lock()

    def cancel(self):
        with self._lock:
            self._cancelled = True
            checkout, self._checkout = self._checkout, None
            archives, self._artifact_previews = self._artifact_previews, {}
        self._cancel()
        if checkout:
            checkout.cleanup()
        for temporary, _ in archives.values():
            temporary.cleanup()

    def preview_artifact(self, name, path, limit=256 * 1024):
        """Export once per viewer; read regular members without extracting them."""
        member_path = PurePosixPath(path)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError("File is outside the artifact")
        # The runner owns a single subprocess. Repeated Enter presses must not
        # start overlapping exports or race ownership of temporary archives.
        with self._preview_lock:
            with self._lock:
                if self._cancelled:
                    raise ValueError("Loading cancelled")
                cached = self._artifact_previews.get(name)
            if cached is None:
                temporary = tempfile.TemporaryDirectory(prefix="bst-tree-artifact-")
                archive = Path(temporary.name) / "artifact.tar"
                try:
                    self.execute([
                        "artifact", "checkout", "--deps", "none", "--no-integrate",
                        "--tar", str(archive), "--", name,
                    ])
                    # As with sources, the worker owns cleanup until bst exits.
                    with self._lock:
                        if self._cancelled:
                            raise ValueError("Loading cancelled")
                        self._artifact_previews[name] = (temporary, archive)
                except BaseException:
                    temporary.cleanup()
                    raise
            else:
                _, archive = cached
            return preview_archive(archive, member_path.as_posix(), limit)

    def pull_artifact(self, name):
        """Download this element, discard old previews and reload its listing."""
        with self._preview_lock:
            with self._lock:
                if self._cancelled:
                    raise ValueError("Loading cancelled")
            command = ["artifact", "pull", "--deps", "none", "--", name]
            self.execute(command)
            with self._lock:
                if self._cancelled:
                    raise ValueError("Loading cancelled")
                cached = self._artifact_previews.pop(name, None)
            if cached:
                cached[0].cleanup()
            result = self.load(name, "artifacts")
            result.text = "Artifact pull completed.\n" + shlex.join(self.base + command) + "\n\n" + result.text
            return result

    def checkout_command(self, name, directory):
        return ["source", "checkout", "--deps", "none", "--directory", str(directory), "--", name]

    def checkout_sources(self, name):
        # Keep ownership in the worker until the command finishes. Closing the
        # screen may cancel the subprocess, but must not race its filesystem writes.
        checkout = tempfile.TemporaryDirectory(prefix="bst-tree-sources-")
        try:
            path = Path(checkout.name) / "sources"
            command = self.checkout_command(name, path)
            self.execute(command)
            with self._lock:
                if self._cancelled:
                    raise ValueError("Loading cancelled")
                self._checkout = checkout
            return Inspection(
                "Temporary source checkout (removed when this viewer closes).\n"
                + shlex.join(self.base + command)
                + "\n\nUse arrows to browse; Enter previews a file. Press i for source information.",
                path,
            )
        except BaseException:
            checkout.cleanup()
            raise

    def execute(self, arguments):
        result = self.run(self.base + arguments, stdout=subprocess.PIPE, text=True, check=False)
        if result.returncode:
            raise ValueError(f"bst failed (exit {result.returncode})")
        return result.stdout

    def show(self, name, format_string):
        result = self.execute(["show", "--deps", "none", "--format", format_string, "--", name])
        if any(field in result for field in ("%{config}", "%{vars}", "%{env}", "%{source-info}", "%{workspace-dirs}")):
            raise ValueError("Installed BuildStream does not support the requested show fields")
        return result

    def load(self, name, section):
        if section == "artifacts":
            details = self.execute(["artifact", "list-contents", "--long", "--", name])
            paths = self.execute(["artifact", "list-contents", "--", name])
            # Use the plain listing for names: long output appends symlink
            # targets, and filenames may themselves contain spaces or " -> ".
            names = [line[1:] for line in paths.splitlines() if line.startswith("\t")]
            rows = [line[1:] for line in details.splitlines() if line.startswith("\t")]
            if names == ["This element has no associated artifacts"]:
                names, rows = [], []
            if len(names) != len(rows):
                raise ValueError("Artifact listing changed while loading; reopen the viewer")
            entries = [ArtifactEntry(path, row, row.startswith("d")) for path, row in zip(names, rows)]
            return Inspection(details, artifacts=entries)
        if section == "build":
            return Inspection(self.show(name, "Configuration:\n%{config}\nVariables:\n%{vars}\nEnvironment:\n%{env}"))
        if section == "sources":
            provenance = self.show(name, "%{source-info}")
            workspace = self.show(name, "%{workspace-dirs}").rstrip("\n")
            if not workspace:
                return Inspection(
                    "Source provenance (URLs/refs depend on the source plugin):\n" + provenance
                    + "\nNo open workspace. Press f (or Load source files) to browse a temporary checkout.\n"
                    + "BuildStream uses cached sources or fetches missing sources from configured remotes/upstream.\n"
                    + "The temporary copy is removed when this viewer closes.\n\nCommand:\n"
                    + shlex.join(self.base + self.checkout_command(name, "<temporary-directory>")),
                    can_checkout=True,
                )
            prefix = "Workspace: "
            if not workspace.startswith(prefix):
                raise ValueError("Unexpected workspace-dirs output from BuildStream")
            path = Path(workspace[len(prefix):])
            if not path.is_absolute() or not path.is_dir():
                raise ValueError(f"Workspace directory is unavailable: {path}")
            return Inspection(
                f"Workspace: {path}\nSelect a file to preview it (read-only).\n\nSource provenance:\n{provenance}",
                path.resolve(),
            )
        raise ValueError(f"Unknown inspection section: {section}")


def preview_file(root, path, limit=256 * 1024):
    """Bounded UTF-8 preview; never follow a link outside the workspace."""
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("File is outside the workspace")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Only regular files can be previewed")
    with path.open("rb") as stream:
        return preview_stream(stream, limit)


def preview_archive(archive, path, limit=256 * 1024):
    with tarfile.open(archive, "r:") as bundle:
        try:
            member = bundle.getmember("./" + path)
        except KeyError:
            try:
                member = bundle.getmember(path)
            except KeyError:
                raise ValueError(f"File is no longer in the artifact: {path}") from None
        # Never resolve symlink or hardlink targets, or open special files.
        if not member.isfile():
            raise ValueError("Only regular files can be previewed; links are not followed")
        with bundle.extractfile(member) as stream:
            return preview_stream(stream, limit)


def preview_stream(stream, limit):
    content = stream.read(limit + 1)
    if b"\x00" in content:
        return "Binary file; text preview unavailable."
    text = content[:limit].decode("utf-8", errors="replace")
    if len(content) > limit:
        text += f"\n\n[Preview truncated at {limit} bytes]"
    return text
