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
"""Conservative virtual-sysroot resolution. Never consult the host loader or cache."""

from collections import defaultdict, deque
import json
from pathlib import PurePosixPath
import posixpath
import re

DEFAULT_PROFILE = {
    "format_version": 1,
    "default_dirs": ["/lib", "/usr/lib"],
    "library_path": [],
    "lib": None,
    "platform": None,
    "cwd": None,
    "inherit_rpath": True,
}
TOKEN = re.compile(r"\$(?:\{([^}]+)\}|([A-Za-z_][A-Za-z_0-9]*))")


def normalize(value):
    return "/" + posixpath.normpath("/" + value.lstrip("/")).lstrip("/")


def load_profile(path=None, library_path=()):
    profile = dict(DEFAULT_PROFILE)
    if path:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict) or set(value) - set(profile):
            raise ValueError("Unknown profile fields")
        profile.update(value)
    if type(profile["format_version"]) is not int or profile["format_version"] != 1:
        raise ValueError("Unsupported profile format_version")
    for key in ("default_dirs", "library_path"):
        if not isinstance(profile[key], list) or any(not isinstance(v, str) or "\x00" in v for v in profile[key]):
            raise ValueError("Profile requires directory lists")
    for key in ("lib", "platform", "cwd"):
        if profile[key] is not None and (not isinstance(profile[key], str) or "\x00" in profile[key]):
            raise ValueError("Invalid profile token/cwd")
    if profile["cwd"] is not None and not profile["cwd"].startswith("/"):
        raise ValueError("Profile cwd must be absolute within the target root")
    if type(profile["inherit_rpath"]) is not bool:
        raise ValueError("inherit_rpath must be boolean")
    profile["library_path"] = list(library_path) + list(profile["library_path"])
    return profile


class Resolver:
    def __init__(self, entries, profile):
        self.entries = entries
        self.profile = profile
        self.by_path = defaultdict(list)
        for entry in entries:
            self.by_path[entry["path"]].append(entry)

    def lookup(self, path):
        found, problems = [], []
        pending = [(normalize(path), [], frozenset())]
        while pending:
            current, chain, seen = pending.pop()
            if current in seen or len(chain) >= 40:
                problems.append("link cycle or link limit: " + current)
                continue
            parts = PurePosixPath(current).parts[1:]
            diverted = False
            for index in range(len(parts)):
                prefix = "/" + "/".join(parts[: index + 1])
                items = self.by_path.get(prefix, [])
                links = [e for e in items if e["kind"] in ("symlink", "hardlink")]
                regular = [e for e in items if e["kind"] not in ("symlink", "hardlink")]
                for link in links:
                    target = link["target"]
                    if "\x00" in target:
                        problems.append("invalid link target")
                        continue
                    if link["kind"] == "hardlink":
                        target = "/" + target.lstrip("/")
                    elif not target.startswith("/"):
                        target = posixpath.join(posixpath.dirname(prefix), target)
                    target = posixpath.join(target, *parts[index + 1 :])
                    pending.append((normalize(target), chain + [link["id"]], seen | {current}))
                if index + 1 < len(parts):
                    if any(e["kind"] != "directory" for e in regular):
                        problems.append("non-directory ancestor: " + prefix)
                    if links and not any(e["kind"] == "directory" for e in regular):
                        diverted = True
                        break
                    if regular and not any(e["kind"] == "directory" for e in regular):
                        diverted = True
                        break
                else:
                    found.extend({"entry": e, "links": chain} for e in regular)
                    diverted = True
            if not diverted:
                problems.append("invalid path: " + current)
        unique = {(r["entry"]["id"], tuple(r["links"])): r for r in found}
        return list(unique.values()), sorted(set(problems))

    def expand(self, value, origin):
        if "\x00" in value:
            raise ValueError("NUL in dynamic path")
        tokens = {"ORIGIN": origin, "LIB": self.profile["lib"], "PLATFORM": self.profile["platform"]}

        def replace(match):
            token = match.group(1) or match.group(2)
            if token not in tokens or tokens[token] is None:
                raise ValueError("Unconfigured dynamic token: " + token)
            return tokens[token]

        expanded = TOKEN.sub(replace, value)
        if "$" in expanded:
            raise ValueError("Unsupported dynamic token: " + value)
        if not expanded.startswith("/"):
            if self.profile["cwd"] is None:
                raise ValueError("Relative/empty dynamic path needs profile cwd: " + repr(value))
            expanded = posixpath.join(self.profile["cwd"], expanded)
        return normalize(expanded)

    def directories(self, entry, inherited):
        elf = entry["elf"]
        origin = posixpath.dirname(entry["path"])
        raw_rpath = elf["rpath"] if elf["runpath"] is None else None
        own = tuple(self.expand(v, origin) for v in raw_rpath or [])
        run = tuple(self.expand(v, origin) for v in elf["runpath"] or [])
        env = tuple(self.expand(v, origin) for v in self.profile["library_path"])
        defaults = () if elf["nodefaultlib"] else tuple(self.expand(v, origin) for v in self.profile["default_dirs"])
        inherited = tuple(inherited) if self.profile["inherit_rpath"] else ()
        directories = tuple(dict.fromkeys(own + inherited + env + run + defaults))
        descendants = tuple(dict.fromkeys(own + inherited)) if self.profile["inherit_rpath"] else ()
        return directories, descendants

    def dependency(self, entry, needed, directories):
        result = {
            "from": entry["id"],
            "needed": needed,
            "status": "missing",
            "searched": [],
            "candidates": [],
            "rejected": [],
        }
        expanded = TOKEN.sub(lambda m: self.expand_token(m, posixpath.dirname(entry["path"])), needed)
        if "/" in expanded:
            paths = [self.expand(expanded, posixpath.dirname(entry["path"]))]
        else:
            paths = [posixpath.join(d, expanded) for d in directories]
        for path in paths:
            result["searched"].append(path)
            items, problems = self.lookup(path)
            if problems:
                result.update(status="unsupported", message="; ".join(problems))
                return result
            if not items:
                continue
            compatible = []
            for item in items:
                other = item["entry"]
                candidate = {"id": other["id"], "owner": other["owner"], "path": other["path"], "links": item["links"]}
                reason = None
                if "elf" not in other:
                    reason = "not-ELF"
                elif other["elf"]["type"] != "ET_DYN":
                    reason = "not-shared-object"
                elif any(other["elf"][k] != entry["elf"][k] for k in ("machine", "class", "little_endian")):
                    reason = "architecture"
                if reason:
                    result["rejected"].append({**candidate, "reason": reason})
                else:
                    compatible.append(candidate)
            result["candidates"] = compatible
            # No stage winner is guessed when artifacts overlap, even if bytes match.
            result["status"] = "ambiguous" if len(items) > 1 else "resolved" if compatible else "incompatible"
            return result
        return result

    def expand_token(self, match, origin):
        token = match.group(1) or match.group(2)
        values = {"ORIGIN": origin, "LIB": self.profile["lib"], "PLATFORM": self.profile["platform"]}
        if token not in values or values[token] is None:
            raise ValueError("Unconfigured dynamic token: " + token)
        return values[token]

    def audit(self, roots=None, expect_machine=None, max_contexts=10000):
        files = {e["id"]: e for e in self.entries if "elf" in e}
        root_findings = []
        if roots:
            selected = []
            for root in roots:
                matches, problems = self.lookup(root)
                if problems or not matches or any("elf" not in m["entry"] for m in matches):
                    raise ValueError("ELF root is missing, invalid or unresolved: " + root)
                if len(matches) > 1:
                    root_findings.append(
                        {"reason": "ambiguous-root", "path": root, "candidates": [m["entry"]["id"] for m in matches]}
                    )
                selected.extend(m["entry"] for m in matches)
        else:
            selected = list(files.values())
        contexts, issues, errors = [], root_findings, []
        for root in sorted({e["id"]: e for e in selected}.values(), key=lambda e: e["id"]):
            pending = deque([(root, ())])
            seen = set()
            while pending:
                entry, inherited = pending.popleft()
                key = (entry["id"], inherited)
                if key in seen:
                    continue
                seen.add(key)
                if len(contexts) >= max_contexts:
                    errors.append({"message": "Dependency contexts exceed --max-contexts"})
                    return contexts, issues, errors
                context = {
                    "root": root["id"],
                    "file": entry["id"],
                    "inherited_rpath": list(inherited),
                    "dependencies": [],
                }
                contexts.append(context)
                if expect_machine and entry["elf"]["machine"] != expect_machine:
                    issues.append({"file": entry["id"], "reason": "unexpected-machine", "expected": expect_machine})
                try:
                    directories, descendants = self.directories(entry, inherited)
                    interpreter = entry["elf"]["interpreter"]
                    wanted = [(n, "needed") for n in entry["elf"]["needed"]]
                    if interpreter:
                        wanted.insert(0, (interpreter, "interpreter"))
                    for needed, kind in wanted:
                        result = self.dependency(entry, needed, directories)
                        result["kind"] = kind
                        context["dependencies"].append(result)
                        if result["status"] == "unsupported":
                            errors.append(result)
                        elif result["status"] != "resolved":
                            issues.append({"root": root["id"], **result})
                        if kind == "needed" and result["status"] == "resolved":
                            pending.append((files[result["candidates"][0]["id"]], descendants))
                except ValueError as error:
                    errors.append({"file": entry["id"], "message": str(error)})
        return contexts, issues, errors
