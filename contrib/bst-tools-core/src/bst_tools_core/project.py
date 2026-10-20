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
"""Public bst CLI framing, scoped graph paths and source/configuration access."""

from collections import deque
from contextlib import contextmanager
from pathlib import Path
import tempfile
import uuid
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from .process import execute


def command_base(directory=None, options=()):
    base = ["bst", "--no-colors", "--strict"]
    if directory is not None:
        base += ["-C", str(directory)]
    for name, value in options:
        base += ["--option", name, value]
    return base


def report_template(fields, marker):
    return marker + "record\n" + "".join(marker + f + "\n%{" + f + "}\n" for f in fields) + marker + "end\n"


def parse_records(output, fields, marker):
    sections = output.split(marker + "record\n")
    if sections[0].strip():
        raise ValueError("Unexpected bst show output")
    records = []
    for section in sections[1:]:
        record = {}
        for index, field in enumerate(fields):
            prefix = marker + field + "\n"
            boundary = marker + (fields[index + 1] if index + 1 < len(fields) else "end") + "\n"
            if not section.startswith(prefix):
                raise ValueError(f"Malformed bst show field: {field}")
            value, separator, remainder = section[len(prefix) :].partition(boundary)
            if not separator or marker in value:
                raise ValueError(f"Missing bst show boundary: {field}")
            if value.strip() == "%{" + field + "}":
                raise ValueError(f"Installed BuildStream has an unsupported show field: {field}")
            record[field] = value
            section = boundary + remainder
        if section.strip() != marker + "end":
            raise ValueError("Trailing data in bst show output")
        records.append(record)
    return records


def dependency_list(value, field, yaml):
    try:
        values = yaml.load(value)
    except YAMLError as error:
        raise ValueError(f"Malformed {field} from bst show: {error}") from error
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ValueError(f"Invalid {field} from bst show")
    return sorted(set(values))


class Project:
    def __init__(self, directory=None, options=(), *, run=execute):
        self.base = command_base(directory, options)
        self.run = run
        self.yaml = YAML(typ="safe")

    def report(self, targets, fields, deps="none"):
        marker = "BST_TOOLS_" + uuid.uuid4().hex + "_"
        output = self.run(
            self.base
            + ["show", "--deps", deps, "--order", "alpha", "--format", report_template(fields, marker), "--", *targets]
        )
        return parse_records(output, fields, marker)

    def graph(self, targets, scope):
        fields = ("name", "kind", "full-key", "state", "build-deps", "runtime-deps")
        nodes = {}
        for record in self.report(targets, fields, "all"):
            name = record["name"].strip()
            if not name or "\n" in name or name in nodes:
                raise ValueError("Invalid or duplicate element name from bst show")
            node = {field: record[field].strip() for field in ("kind", "full-key", "state")}
            for field in ("build-deps", "runtime-deps"):
                node[field] = dependency_list(record[field], field, self.yaml)
            nodes[name] = node
        roots = self.names(targets, "none")
        selected = set(nodes) if scope == "all" else set(self.names(targets, scope))
        if not roots or any(name not in nodes for name in selected):
            raise ValueError("Dependency graph changed while loading; retry")
        validate_graph(nodes, roots)
        return Graph(nodes, roots, selected, scope)

    def names(self, targets, deps):
        names = [record["name"].strip() for record in self.report(targets, ("name",), deps)]
        if any(not name or "\n" in name for name in names) or len(set(names)) != len(names):
            raise ValueError("Invalid or duplicate element name from bst show")
        return names

    def listings(self, names):
        plain = self.run(self.base + ["artifact", "list-contents", "--", *names])
        details = self.run(self.base + ["artifact", "list-contents", "--long", "--", *names])
        return plain, details

    def checkout_command(self, name):
        return self.base + ["artifact", "checkout", "--deps", "none", "--no-integrate", "--tar", "-", "--", name]

    def commands(self, name):
        return {
            "configuration": self.base + ["show", "--deps", "none", "--format", "%{config}", "--", name],
            "sources": self.base
            + ["source", "checkout", "--deps", "none", "--directory", "<source-directory>", "--", name],
            "artifact": self.base
            + [
                "artifact",
                "checkout",
                "--deps",
                "none",
                "--no-integrate",
                "--directory",
                "<artifact-directory>",
                "--",
                name,
            ],
        }

    def select(self, targets, deps, *, workspaces=False):
        fields = ("name", "workspace-dirs") if workspaces else ("name",)
        selected = {}
        for record in self.report(targets, fields, deps):
            name = record["name"].strip()
            if not name or "\n" in name or name in selected:
                raise ValueError("Invalid or duplicate element name from bst show")
            selected[name] = record.get("workspace-dirs", "").rstrip("\n")
        return selected

    @contextmanager
    def sources(self, name, workspace):
        if workspace:
            prefix = "Workspace: "
            if not workspace.startswith(prefix):
                raise ValueError("Unexpected workspace-dirs output from BuildStream")
            root = Path(workspace[len(prefix) :])
            if not root.is_absolute() or not root.is_dir():
                raise ValueError(f"Workspace directory is unavailable: {root}")
            yield root.resolve(), "workspace"
            return
        with tempfile.TemporaryDirectory(prefix="bst-tools-sources-") as temporary:
            destination = Path(temporary) / "sources"
            self.run(
                self.base
                + [
                    "source",
                    "checkout",
                    "--deps",
                    "none",
                    "--directory",
                    str(destination),
                    "--",
                    name,
                ]
            )
            yield checkout_root(destination, name), "checkout"

    @contextmanager
    def configuration(self, name):
        records = self.report([name], ("config", "vars", "env"))
        if len(records) != 1:
            raise ValueError("Expected one configuration record from bst show")
        with tempfile.TemporaryDirectory(prefix="bst-tools-config-") as temporary:
            root = Path(temporary)
            for field, content in records[0].items():
                # bst show adds a framing newline; preserve the field's own lines.
                (root / (field + ".yaml")).write_text(content.removesuffix("\n"), encoding="utf-8")
            yield root, "resolved"

    def paths(self, targets, deps="all"):
        roots = self.names(targets, "none")
        nodes = {}
        for record in self.report(targets, ("name", "build-deps", "runtime-deps"), "all"):
            name = record["name"].strip()
            if not name or name in nodes:
                raise ValueError("Invalid or duplicate element in dependency graph")
            nodes[name] = {
                field: dependency_list(record[field], field, self.yaml) for field in ("build-deps", "runtime-deps")
            }
        validate_graph(nodes, roots)
        return Paths.for_scope(nodes, roots, deps)


def validate_graph(nodes, roots):
    if any(root not in nodes for root in roots) or any(
        dep not in nodes for node in nodes.values() for field in ("build-deps", "runtime-deps") for dep in node[field]
    ):
        raise ValueError("Dependency graph changed while loading; retry")


class Graph:
    def __init__(self, nodes, roots, selected, scope):
        self.nodes, self.selected = nodes, selected
        self._paths = Paths.for_scope(nodes, roots, scope)

    def paths(self, name):
        return self._paths.to(name)


class Paths:
    @classmethod
    def for_scope(cls, nodes, roots, scope):
        adjacency = {
            name: sorted(set(node["build-deps"] + node["runtime-deps"])) if scope == "all" else node["runtime-deps"]
            for name, node in nodes.items()
        }
        root_edges = (
            {root: nodes[root]["build-deps"] if scope == "build" else [] for root in roots}
            if scope in ("none", "build")
            else None
        )
        return cls(adjacency, roots, root_edges)

    def __init__(self, adjacency, roots, root_edges=None):
        self.predecessors = []
        for root in roots:
            parents = {root: None}
            queue = deque([root])
            while queue:
                parent = queue.popleft()
                children = root_edges[root] if root_edges is not None and parent == root else adjacency[parent]
                for child in children:
                    if child not in parents:
                        parents[child] = parent
                        queue.append(child)
            self.predecessors.append(parents)

    def to(self, name):
        paths = []
        for parents in self.predecessors:
            if name not in parents:
                continue
            path, current = [], name
            while current is not None:
                path.append(current)
                current = parents[current]
            paths.append(list(reversed(path)))
        return paths


def checkout_root(directory, name):
    """Remove bst's element/junction wrapper directories, never a source directory."""
    root = directory
    for _ in name.split(":"):
        entries = list(root.iterdir())
        if not entries and root == directory:
            return root  # Source-less elements have an empty checkout.
        if len(entries) != 1 or entries[0].is_symlink() or not entries[0].is_dir():
            raise ValueError("Unexpected bst source checkout layout")
        root = entries[0]
    return root
