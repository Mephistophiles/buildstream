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
"""Public BuildStream CLI adapter, framed metadata and scoped dependency paths."""

from collections import deque
import uuid

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .process import execute


class Project:
    def __init__(self, directory=None, options=(), *, run=execute):
        self.base = ["bst", "--no-colors", "--strict"]
        if directory is not None:
            self.base += ["-C", str(directory)]
        for name, value in options:
            self.base += ["--option", name, value]
        self.run = run

    def report(self, targets, fields, deps="none"):
        marker = "BST_ELF_" + uuid.uuid4().hex + "_"
        template = marker + "record\n"
        for field in fields:
            template += marker + field + "\n%{" + field + "}\n"
        template += marker + "end\n"
        output = self.run(
            self.base + ["show", "--deps", deps, "--order", "alpha", "--format", template, "--", *targets]
        )
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
                    raise ValueError(f"Installed BuildStream does not support show field: {field}")
                record[field] = value
                section = boundary + remainder
            if section.strip() != marker + "end":
                raise ValueError("Trailing data in bst show output")
            records.append(record)
        return records

    def graph(self, targets, scope):
        fields = ("name", "kind", "full-key", "state", "artifact-cas-digest", "build-deps", "runtime-deps")
        nodes = {}
        yaml = YAML(typ="safe")
        for record in self.report(targets, fields, "all"):
            name = record["name"].strip()
            if not name or "\n" in name or name in nodes:
                raise ValueError("Invalid or duplicate element name from bst show")
            node = {field: record[field].strip() for field in ("kind", "full-key", "state", "artifact-cas-digest")}
            for field in ("build-deps", "runtime-deps"):
                try:
                    values = yaml.load(record[field])
                except YAMLError as error:
                    raise ValueError(f"Malformed dependency list: {error}") from error
                if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                    raise ValueError(f"Invalid {field} from bst show")
                node[field] = sorted(set(values))
            nodes[name] = node
        roots = self.names(targets, "none")
        selected = set(nodes) if scope == "all" else set(self.names(targets, scope))
        if (
            not roots
            or any(name not in nodes for name in (*roots, *selected))
            or any(
                dep not in nodes
                for node in nodes.values()
                for field in ("build-deps", "runtime-deps")
                for dep in node[field]
            )
        ):
            raise ValueError("Dependency graph changed while loading; retry")
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


class Graph:
    def __init__(self, nodes, roots, selected, scope):
        self.nodes, self.selected = nodes, selected
        self.predecessors = []
        for root in roots:
            parents = {root: None}
            queue = deque([root])
            while queue:
                parent = queue.popleft()
                node = nodes[parent]
                if scope == "none":
                    children = []
                elif scope == "all":
                    children = sorted(set(node["build-deps"] + node["runtime-deps"]))
                elif scope == "build" and parent == root:
                    children = node["build-deps"]
                else:
                    children = node["runtime-deps"]
                for child in children:
                    if child not in parents:
                        parents[child] = parent
                        queue.append(child)
            self.predecessors.append(parents)

    def paths(self, name):
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
