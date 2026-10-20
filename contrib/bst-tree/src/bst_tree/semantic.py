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
"""Compare dependency roles and retained routes relative to explicit targets."""

from collections import deque

from .keygraph import KeyGraph, members

CATEGORIES = ("removed", "role_changed", "added", "retained", "unchanged")
LABELS = {
    "added": "Added", "removed": "Removed", "role_changed": "Role changed",
    "retained": "Still reachable", "unchanged": "Unchanged",
}
SCOPES = ("all", "run", "build")
NOTE = "Declared element reachability; not artifact file contents or a prediction that a build will run."


def role_label(roles):
    return "/".join(roles) or "none"


class SemanticDiff:
    """Cache compact closures; compute witness paths only when requested."""

    def __init__(self, old, new, targets=None):
        self.graphs = (old, new)
        self.names = sorted(old.nodes.keys() | new.nodes.keys())
        self.index = {name: i for i, name in enumerate(self.names)}
        self.keys = tuple(KeyGraph(g, self.names) for g in self.graphs)
        common = set(old.targets) & set(new.targets)
        self.targets = sorted(common if targets is None else set(targets))
        unknown = set(self.targets) - common
        if unknown:
            raise ValueError("Semantic targets must be requested in both snapshots: " + ", ".join(sorted(unknown)))
        self.targets_added = sorted(set(new.targets) - set(old.targets)) if targets is None else []
        self.targets_removed = sorted(set(old.targets) - set(new.targets)) if targets is None else []
        self.changed_edges = [(p, c) for p, c in sorted(old.edges.keys() | new.edges.keys())
                              if old.edges.get((p, c)) != new.edges.get((p, c))]
        self.adjacency = tuple(g.adjacency() for g in self.graphs)
        self._target_cache = {}
        self._path_cache = {}

    def _target(self, target):
        if target not in self.targets:
            raise ValueError("Unknown semantic comparison target: " + target)
        if target in self._target_cache:
            return self._target_cache[target]
        root = self.index[target]
        closures = {role: tuple(k.runtime[root] if role == "run" else k.inputs[root] for k in self.keys)
                    for role in ("run", "build")}
        routes = {"run": 0, "build": 0}
        evidence = []
        for parent, child in self.changed_edges:
            p, c = self.index[parent], self.index[child]
            covered = {}
            for role in ("run", "build"):
                used = []
                phases = []
                for side, graph in enumerate(self.graphs):
                    kinds = graph.edges.get((parent, child), ())
                    runtime_parent = bool(closures[role][side] & (1 << p)) or (
                        parent == target and (role == "run" or self.keys[side].build_runtime_root[root])
                    )
                    phases.append((parent == target and role in kinds, runtime_parent and "run" in kinds))
                    used.append(any(phases[-1]))
                if phases[0] == phases[1]:
                    continue
                mask = 0
                for side in (0, 1):
                    if used[side]:
                        mask |= self.keys[side].runtime[c] | (1 << c)
                mask &= ~(1 << root)
                routes[role] |= mask
                covered[role] = tuple(used)
            if covered:
                evidence.append((parent, child, covered))
        value = closures, routes, evidence
        if len(self._target_cache) >= 8:
            self._target_cache.pop(next(iter(self._target_cache)))
        self._target_cache[target] = value
        return value

    def masks(self, target, scope="all"):
        if scope not in SCOPES:
            raise ValueError("Unknown semantic scope: " + scope)
        closures, routes, _ = self._target(target)
        roles = ("run", "build") if scope == "all" else (scope,)
        before = after = changed = affected = 0
        for role in roles:
            old, new = closures[role]
            before |= old
            after |= new
            changed |= old ^ new
            affected |= routes[role]
        common = before & after
        role_changed = common & changed
        retained = common & ~role_changed & affected
        return {
            "added": after & ~before,
            "removed": before & ~after,
            "role_changed": role_changed,
            "retained": retained,
            "unchanged": common & ~role_changed & ~retained,
        }

    def roles(self, target, name, scope="all"):
        closures, _, _ = self._target(target)
        bit = 1 << self.index[name]
        roles = ("build", "run") if scope == "all" else (scope,)
        return tuple([role for role in roles if closures[role][side] & bit] for side in (0, 1))

    def changes(self, target, scope="all", *, show_all=False, limit=None):
        masks = self.masks(target, scope)
        result = []
        for category in CATEGORIES:
            if category == "unchanged" and not show_all:
                continue
            for name in members(masks[category], self.names, limit):
                before, after = self.roles(target, name, scope)
                result.append({"name": name, "change": category, "old": before, "new": after})
        return result

    def report(self, scope="all", *, show_all=False, limit=None):
        if scope not in SCOPES:
            raise ValueError("Unknown semantic scope: " + scope)
        targets = []
        summary = {c: 0 for c in CATEGORIES}
        for target in self.targets:
            counts = {c: mask.bit_count() for c, mask in self.masks(target, scope).items()}
            for category, count in counts.items():
                summary[category] += count
            targets.append({"target": target, "summary": counts,
                            "changes": self.changes(target, scope, show_all=show_all, limit=limit)})
        return {
            "format_version": 1, "comparison": "semantic", "scope": scope, "summary": summary,
            "targets_added": self.targets_added, "targets_removed": self.targets_removed,
            "targets": targets, "related_limit": limit,
            "notes": [NOTE, "Targets requested on only one side are listed separately; their closures are not compared."],
        }

    def path(self, side, target, name, role):
        """One deterministic shortest witness in a role; build then runtime, never build then build."""
        if role not in ("run", "build"):
            raise ValueError("Witness paths require run or build scope")
        cache_key = side, target, role
        if cache_key not in self._path_cache:
            adjacency = self.adjacency[side]
            if target not in adjacency:
                return None
            root = target, role
            parents = {root: None}
            pending = deque([root])
            while pending:
                parent, phase = pending.popleft()
                for child, kinds in adjacency[parent]:
                    state = child, "run"
                    if phase in kinds and state not in parents:
                        parents[state] = (parent, phase)
                        pending.append(state)
            if len(self._path_cache) >= 8:
                self._path_cache.pop(next(iter(self._path_cache)))
            self._path_cache[cache_key] = parents
        parents = self._path_cache[cache_key]
        state = name, "run"
        if name == target or state not in parents:
            return None
        result = []
        while state is not None:
            result.append(state[0])
            state = parents[state]
        return list(reversed(result))

    def route_changes(self, target, name, scope="all"):
        _, _, evidence = self._target(target)
        bit = 1 << self.index[name]
        roles = ("run", "build") if scope == "all" else (scope,)
        result = []
        for parent, child, covered in evidence:
            c = self.index[child]
            relevant = [role for role in roles if any(
                used and (name == child or self.keys[side].runtime[c] & bit)
                for side, used in enumerate(covered.get(role, (False, False)))
            )]
            if relevant:
                result.append({"from": parent, "to": child, "roles": relevant,
                               "old": sorted(self.graphs[0].edges.get((parent, child), ())),
                               "new": sorted(self.graphs[1].edges.get((parent, child), ()))})
        return result


def has_semantic_changes(report):
    return bool(report["targets_added"] or report["targets_removed"]
                or any(report["summary"][c] for c in ("added", "removed", "role_changed")))


def render_semantic(report, *, color=False):
    def paint(text, category):
        code = {"added": "32", "removed": "31", "role_changed": "33", "retained": "36"}.get(category)
        return f"\033[{code}m{text}\033[0m" if color and code else text

    summary = report["summary"]
    lines = [f"Dependency effects (scope={report['scope']}): "
             f"+{summary['added']} -{summary['removed']} ~{summary['role_changed']}; "
             f"still reachable: {summary['retained']}"]
    for key, label in (("targets_added", "Targets only in new snapshot"),
                       ("targets_removed", "Targets only in old snapshot")):
        if report[key]:
            lines.append(label + ": " + ", ".join(report[key]))
    for target in report["targets"]:
        if not target["changes"]:
            continue
        lines.append("\n" + target["target"])
        for category in CATEGORIES:
            entries = [r for r in target["changes"] if r["change"] == category]
            if not entries:
                continue
            count = target["summary"][category]
            lines.append(f"  {LABELS[category]} ({count}):")
            mark = {"added": "+", "removed": "-", "role_changed": "~", "retained": "=", "unchanged": " "}[category]
            for record in entries:
                roles = role_label(record["old"]) + " -> " + role_label(record["new"])
                lines.append(paint(f"    {mark} {record['name']} [{roles}]", category))
            if len(entries) < count:
                lines.append(f"    … {count - len(entries)} more; use --format json or --tui")
    if not has_semantic_changes(report):
        lines.append("\nNo dependency membership or role changes.")
    if not report["targets"]:
        lines.append("No common requested targets; closures cannot be compared.")
    lines.append("\n" + NOTE)
    return "\n".join(lines)
