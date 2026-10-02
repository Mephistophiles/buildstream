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
from pathlib import Path
import stat
import subprocess

from .adapter import command_base


@dataclass
class Inspection:
    text: str
    workspace: Path | None = None


class ProjectInspector:
    def __init__(self, directory=None, options=(), *, run=subprocess.run, cancel=None):
        self.base = command_base(directory, options)
        self.run = run
        self.cancel = cancel or (lambda: None)

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
            return Inspection(self.execute(["artifact", "list-contents", "--long", "--", name]))
        if section == "build":
            return Inspection(self.show(name, "Configuration:\n%{config}\nVariables:\n%{vars}\nEnvironment:\n%{env}"))
        if section == "sources":
            provenance = self.show(name, "%{source-info}")
            workspace = self.show(name, "%{workspace-dirs}").rstrip("\n")
            if not workspace:
                return Inspection(
                    "Source provenance (URLs/refs depend on the source plugin):\n" + provenance
                    + "\nNo open workspace. Source files are not loaded. No checkout or fetch was run."
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
        content = stream.read(limit + 1)
    if b"\x00" in content:
        return "Binary file; text preview unavailable."
    text = content[:limit].decode("utf-8", errors="replace")
    if len(content) > limit:
        text += f"\n\n[Preview truncated at {limit} bytes]"
    return text
