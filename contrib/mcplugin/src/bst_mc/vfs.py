# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""MC extfs list/copyout protocol, using only the public BuildStream CLI.

The graph is finite: dependency directories link to canonical element directories.
Expensive inspection happens only in copyout (F3/F5/Enter), never in list.
MC owns extracted files, including nested source/artifact tar archives, so no
persistent checkout cache or background server is needed.
"""

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote, unquote

from . import __version__
from ._bst import capture, Inspector


SCOPES = ("all", "build", "run")
ACTIONS = (
    "element.json",
    "source-info.txt",
    "build-commands.txt",
    "sources.tar",
    "artifact.tar",
    "paths.txt",
)
HELP = """BuildStream project in Midnight Commander (read-only)

GETTING STARTED
Open all/targets/, choose a target, then follow dependencies/.
all/ contains the full graph; run/ contains runtime dependencies; build/
contains direct build dependencies and their runtime closure, retaining roots.
elements/ groups canonical element directories by their project paths.
Junctions are folders marked [junction]; nested junctions form nested folders.

NAVIGATION
Enter opens directories and dependency links; '..' goes up.
Alt-y returns in directory history; Ctrl-s searches panel names.
dependencies/ and reverse-dependencies/ link within the selected scope.
tree.txt shows build/run edges; [see above] bounds shared subtrees and cycles.
paths.txt shows one shortest path from each applicable target in this scope.

INSPECTION
F3 on element.json: original name, kind, key, provenance, workspace presence.
F3 on source-info.txt: source provenance from bst show.
F3 on build-commands.txt: resolved configuration, variables, and environment.
Build commands are effective settings, not the original YAML or a build log.
Enter sources.tar: bst source checkout --deps none --include-build-scripts --tar ...
Enter artifact.tar: pull if not cached, then
bst artifact checkout --deps none --no-integrate --tar ...
Inside an archive, Enter browses folders, F3 views files, and F5 copies them
into the other panel. Binary files can also be opened in MC's viewer.
Missing dotfiles? Enable Show hidden files in MC's panel options (Alt-. with
its default keymap). The plugin does not filter hidden archive entries.

DOWNLOADS AND LIMITATIONS
The entire selected element is exported on first access, without dependencies.
Large exports can take time and temporary disk space.
Source checkout may fetch sources and follows open-workspace semantics.
artifact.tar pulls missing artifacts from configured remotes with
bst artifact pull --deps none. Cached artifacts skip the pull.
Downloads remain in BuildStream's cache after MC closes. Neither action
builds, tracks sources, or runs integration commands. Failures can be retried.
Do not edit nested archives: writing back to the project is unsupported.

REFRESH AND TROUBLESHOOTING
All commands use the bookmark's project directory/options and strict mode.
Bookmarks are live project references, not snapshots. Separate queries can
observe different project states. Unloaded virtual files show a size of zero.
MC caches listings and exports. Leave the VFS and free it through MC's active
VFS list, or restart MC; Ctrl-r alone may reuse cached results.
If setup fails, run bst-mc --install-mc and restart MC. Check that bst is on
PATH and can load the project with its required plugins and options.
Use bst-mc --help for launch options; bstmc --help for protocol diagnostics.
"""


def component(name):
    """Keep slashes, junction colons, whitespace and arrows out of extfs names."""
    encoded = quote(name, safe="")
    return encoded.replace(".", "%2E") if name in (".", "..") else encoded


def element_path(name):
    """Render junction boundaries as marked directories, without losing names."""
    sections = re.split(r"(::?)", name)
    directories = []
    for index in range(0, len(sections), 2):
        parts = sections[index].split("/")
        if any(part in ("", ".", "..") or "\x00" in part for part in parts):
            raise ValueError("Invalid element path")
        encoded = [component(part) for part in parts]
        if index + 1 < len(sections):
            # Public bst names use ':'. Keep a literal '::' distinguishable
            # too, rather than silently normalizing what we pass back to bst.
            encoded[-1] += " [junction]" if sections[index + 1] == ":" else " [junction x2]"
        directories.extend(encoded)
    return "/".join(directories)


def element_name(path):
    """Decode only our canonical VFS spelling, including junction markers."""
    decoded = []
    parts = path.split("/")
    for index, part in enumerate(parts):
        separator = "/" if index + 1 < len(parts) else ""
        for marker, boundary in ((" [junction]", ":"), (" [junction x2]", "::")):
            if part.endswith(marker):
                part, separator = part[:-len(marker)], boundary
                break
        decoded.append(unquote(part) + separator)
    name = "".join(decoded)
    if element_path(name) != path:
        raise ValueError("Invalid encoded element name")
    return name


def run_bst(arguments, **kwargs):
    # MC treats *any* extfs stderr as an error dialog, including successful bst
    # progress messages. Keep diagnostics for failures only.
    result = subprocess.run(arguments, stderr=subprocess.PIPE, **kwargs)
    if result.returncode and result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return result


@dataclass
class Entry:
    path: str
    kind: str = "file"
    target: str | None = None
    size: int = 0

    def listing(self):
        mode = {"file": "-r--r--r--", "dir": "dr-xr-xr-x", "link": "lrwxrwxrwx"}[self.kind]
        suffix = f" -> {self.target}" if self.target else ""
        return f"{mode} 1 0 0 {self.size} 01-01-2000 00:00 {self.path}{suffix}"


class Project:
    def __init__(self, descriptor, *, run=run_bst):
        if not isinstance(descriptor, dict) or type(descriptor.get("format_version")) is not int:
            raise ValueError("Expected a version-1 .bstmc bookmark")
        if descriptor["format_version"] != 1:
            raise ValueError("Unsupported .bstmc bookmark version")
        directory = descriptor.get("directory")
        targets, options = descriptor.get("targets"), descriptor.get("options", [])
        if not isinstance(directory, str) or not Path(directory).is_absolute() or not Path(directory).is_dir():
            raise ValueError("Bookmark directory must be an existing absolute project path")
        if not isinstance(targets, list) or any(not isinstance(t, str) or not t for t in targets):
            raise ValueError("Bookmark targets must be a list of element names")
        if not isinstance(options, list) or any(
            not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(v, str) for v in pair)
            for pair in options
        ):
            raise ValueError("Bookmark options must be [name, value] pairs")
        self.directory, self.targets, self.options = directory, targets, options
        self.run = run
        self.inspector = Inspector(directory, options, run=run)
        self._graph = None

    @property
    def graph(self):
        if self._graph is None:
            self._graph = capture(self.targets, self.directory, self.options, run=self.run)
        return self._graph

    def entries(self):
        emitted = {}
        for entry in self._entries():
            # extfs needs explicit parent directories; several elements can
            # share them, so emit each exactly once, before its children.
            parents = reversed(PurePosixPath(entry.path).parents)
            for candidate in [*(Entry(str(p), "dir") for p in parents if str(p) != "."), entry]:
                existing = emitted.get(candidate.path)
                if existing is None:
                    emitted[candidate.path] = candidate
                    yield candidate
                elif existing != candidate:
                    raise ValueError(f"Conflicting virtual paths: {candidate.path}")

    def _entries(self):
        yield Entry("README.txt", size=len(HELP.encode()))
        for scope in SCOPES:
            graph = self.graph.scoped(scope)
            yield Entry(scope, "dir")
            yield Entry(f"{scope}/tree.txt")
            yield Entry(f"{scope}/targets", "dir")
            yield Entry(f"{scope}/elements", "dir")
            for name in graph.targets:
                encoded = element_path(name)
                path = f"{scope}/targets/{encoded}"
                yield Entry(path, "link", posixpath.relpath(f"{scope}/elements/{encoded}", posixpath.dirname(path)))
            for name in sorted(graph.nodes):
                root = f"{scope}/elements/{element_path(name)}"
                yield Entry(root, "dir")
                for action in ACTIONS:
                    yield Entry(f"{root}/{action}")
                yield Entry(f"{root}/dependencies", "dir")
                yield Entry(f"{root}/reverse-dependencies", "dir")
            for (parent, child), kinds in sorted(graph.edges.items()):
                label = "+".join(sorted(kinds))
                for name, destination, folder in (
                    (parent, child, "dependencies"),
                    (child, parent, "reverse-dependencies"),
                ):
                    encoded = element_path(destination)
                    path = f"{scope}/elements/{element_path(name)}/{folder}/{encoded} [{label}]"
                    yield Entry(
                        path,
                        "link",
                        posixpath.relpath(f"{scope}/elements/{encoded}", posixpath.dirname(path)),
                    )

    def tree(self, scope):
        graph = self.graph.scoped(scope)
        adjacency, expanded = graph.adjacency(), set()
        lines = []
        pending = [(root, "", 0) for root in reversed(graph.targets)]
        while pending:
            name, edge, depth = pending.pop()
            repeated = name in expanded
            lines.append("  " * depth + name + edge + (" [see above]" if repeated else ""))
            if not repeated:
                expanded.add(name)
                for child, kinds in reversed(adjacency[name]):
                    pending.append((child, " [" + "+".join(sorted(kinds)) + "]", depth + 1))
        return "\n".join(lines) + "\n"

    def copyout(self, member, destination):
        # MC resolves VFS symlinks before copyout. Reject non-canonical paths,
        # rather than allowing a caller to select a different local file.
        parts = member.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("Invalid VFS member path")
        if member == "README.txt":
            content = HELP
        elif len(parts) == 2 and parts[0] in SCOPES and parts[1] == "tree.txt":
            content = self.tree(parts[0])
        elif len(parts) >= 4 and parts[0] in SCOPES and parts[1] == "elements" and parts[-1] in ACTIONS:
            scope, encoded, action = parts[0], "/".join(parts[2:-1]), parts[-1]
            name = element_name(encoded)
            if action in ("sources.tar", "artifact.tar"):
                self.export(name, action, destination)
                return
            if action == "source-info.txt":
                content = self.inspector.show(name, "%{source-info}")
            elif action == "build-commands.txt":
                content = self.inspector.show(name, "Configuration:\n%{config}\nVariables:\n%{vars}\nEnvironment:\n%{env}")
            else:
                graph = self.graph.scoped(scope)
                if name not in graph.nodes:
                    raise ValueError("Element is no longer in this graph; reopen the project")
                if action == "element.json":
                    content = json.dumps({"name": name, **graph.nodes[name]}, ensure_ascii=False, indent=2) + "\n"
                else:
                    content = "\n".join(" -> ".join(path) for path in graph.paths_to(name)) + "\n"
        else:
            raise ValueError(f"Unknown VFS member: {member}")
        Path(destination).write_text(content, encoding="utf-8")

    def export(self, name, action, destination):
        if action == "artifact.tar":
            # A cached artifact must remain browsable without configured remotes.
            # Failed builds can also have cached artifacts worth inspecting.
            state = self.inspector.show(name, "%{state}").strip()
            if state not in ("cached", "failed"):
                self.inspector.execute(["artifact", "pull", "--deps", "none", "--", name])
        # BuildStream requires a nonexistent output; MC provides an existing
        # temporary file. Stage separately and copy only a successful export.
        with tempfile.TemporaryDirectory(prefix="bst-mc-export-") as temporary:
            archive = Path(temporary) / "contents.tar"
            command = ["source" if action == "sources.tar" else "artifact", "checkout", "--deps", "none"]
            if action == "artifact.tar":
                command.append("--no-integrate")
            else:
                command.append("--include-build-scripts")
            self.inspector.execute([*command, "--tar", str(archive), "--", name])
            shutil.copyfile(archive, destination)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--help"], ["-h"]):
        print("Usage: bstmc list BOOKMARK | bstmc copyout BOOKMARK MEMBER DESTINATION\n"
              "Read-only MC extfs helper. Use bst-mc to launch MC or create bookmarks.\n"
              "list prints the virtual filesystem; copyout exports one member.\n"
              "Example: bstmc copyout project.bstmc all/elements/app.bst/element.json /tmp/element.json")
        return 0
    if args == ["--version"]:
        print(f"bst-mc {__version__}")
        return 0
    try:
        if not args or args[0] not in ("list", "copyout"):
            raise ValueError("Read-only VFS: only list and copyout are supported")
        if len(args) != (2 if args[0] == "list" else 4):
            raise ValueError("Usage: bstmc list BOOKMARK | bstmc copyout BOOKMARK MEMBER DESTINATION")
        project = Project(json.loads(Path(args[1]).read_text(encoding="utf-8")))
        if args[0] == "list":
            # Do not emit a partial archive if loading fails midway.
            print("\n".join(entry.listing() for entry in project.entries()))
        else:
            project.copyout(args[2], args[3])
        return 0
    except (OSError, ValueError) as error:
        print(f"bstmc: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
