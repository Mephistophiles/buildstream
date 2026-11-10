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
ROLES = ("build", "run")
SCOPES = ("all", *ROLES)
NOTE = "Declared element reachability; not artifact file contents or a prediction that a build will run."


def role_label(roles):
    return "/".join(roles) or "none"


def role_transition(before, after, *, arrow=" -> "):
    old, new = role_label(before), role_label(after)
    return old if old == new else old + arrow + new


class SemanticDiff:
    """Cache compact closures; compute witness paths only when requested."""

    def __init__(self, old, new, targets=None):
        self.graphs = (old, new)
        self.names = sorted(old.nodes.keys() | new.nodes.keys())
        self.index = {name: i for i, name in enumerate(self.names)}
        self.keys = tuple(KeyGraph(g, self.names) for g in self.graphs)
        common_roots = set(old.targets) & set(new.targets)
        self.targets = sorted(common_roots if targets is None else set(targets))
        unknown = [target for target in self.targets if target not in old.nodes or target not in new.nodes]
        if unknown:
            raise ValueError("Semantic targets must be present in both snapshots: " + ", ".join(unknown))
        self.targets_added = sorted(set(new.targets) - set(old.targets)) if targets is None else []
        self.targets_removed = sorted(set(old.targets) - set(new.targets)) if targets is None else []
        self.changed_edges = [(p, c) for p, c in sorted(old.edges.keys() | new.edges.keys())
                              if old.edges.get((p, c)) != new.edges.get((p, c))]
        self.adjacency = tuple(g.adjacency() for g in self.graphs)
        self._target_cache = {}
        self._path_cache = {}
        self._declared_edges = {}
        self._declared_targets = self.targets if targets is not None else None

    def declared_edges(self, scope, *, target=None):
        key = scope, target
        if key in self._declared_edges:
            return self._declared_edges[key]
        if scope != "all":
            result = self._scoped_edges(scope, [target] if target is not None else self.targets)
        else:
            roots = [target] if target is not None else self._declared_targets
            result = self._raw_edges(roots)
        if len(self._declared_edges) >= 8:
            self._declared_edges.pop(next(iter(self._declared_edges)))
        self._declared_edges[key] = result
        return result

    def _scoped_edges(self, role, targets):
        # Project a changed edge onto the selected closure, even when a build
        # closure reaches it through a transitive runtime edge.
        usage = {}
        for target in targets:
            for parent, child, covered in self._target(target)[2]:
                if role in covered:
                    before, after = usage.get((parent, child), (False, False))
                    old_use, new_use = covered[role]
                    usage[parent, child] = before or old_use, after or new_use
        return [
            {"from": parent, "to": child, "old": [role] if before else [], "new": [role] if after else []}
            for (parent, child), (before, after) in sorted(usage.items())
        ]

    def _raw_edges(self, roots):
        owners = []
        if roots is not None:
            # Follow both types of edges, independently in each snapshot.
            for adjacency in self.adjacency:
                reached = set(roots)
                pending = list(roots)
                while pending:
                    for child, _ in adjacency[pending.pop()]:
                        if child not in reached:
                            reached.add(child)
                            pending.append(child)
                owners.append(reached)
        result = []
        for edge in self.changed_edges:
            parent, child = edge
            before, after = (graph.edges.get(edge, frozenset()) for graph in self.graphs)
            if owners and not ((before and parent in owners[0]) or (after and parent in owners[1])):
                continue
            result.append({"from": parent, "to": child, "old": sorted(before), "new": sorted(after)})
        return result

    def _closure(self, root, role):
        return tuple((key.runtime if role == "run" else key.inputs)[root] for key in self.keys)

    def _target(self, target):
        if target not in self.targets:
            raise ValueError("Unknown semantic comparison target: " + target)
        if target in self._target_cache:
            return self._target_cache[target]
        root = self.index[target]
        closures = {role: self._closure(root, role) for role in ROLES}
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

    def counts(self, target, scope):
        return {category: mask.bit_count() for category, mask in self.masks(target, scope).items()}

    def role_records(self, target, scope, *, show_all=False):
        return [
            dict(record, role=role)
            for role in (ROLES if scope == "all" else (scope,))
            for record in self.changes(target, role, show_all=show_all)
        ]

    def nested_closure_deltas(self, consumer, *, limit=None):
        """Compare membership below a consumer without recomputing route evidence."""
        if any(consumer not in graph.nodes for graph in self.graphs):
            return []
        root = self.index[consumer]
        result = []
        for role in ROLES:
            before, after = self._closure(root, role)
            if before == after:
                continue
            removed, added = before & ~after, after & ~before
            changes = [
                {"name": name, "change": change, "old": old, "new": new}
                for mask, change, old, new in (
                    (removed, "removed", [role], []),
                    (added, "added", [], [role]),
                )
                for name in members(mask, self.names, limit)
            ]
            result.append({
                "target": consumer, "scope": role,
                "summary": {"removed": removed.bit_count(), "added": added.bit_count()},
                "changes": changes,
            })
        return result

    def report(self, scope="all", *, show_all=False, limit=None, include_edges=True):
        if scope not in SCOPES:
            raise ValueError("Unknown semantic scope: " + scope)
        targets = []
        summary = dict.fromkeys(CATEGORIES, 0)
        summary_by_role = {role: dict.fromkeys(CATEGORIES, 0) for role in ROLES}
        for target in self.targets:
            counts = self.counts(target, scope)
            for category, count in counts.items():
                summary[category] += count
            entry = {"target": target, "summary": counts,
                     "changes": self.changes(target, scope, show_all=show_all, limit=limit)}
            if scope == "all":
                entry["by_role"] = {}
                for role in ROLES:
                    counts = self.counts(target, role)
                    for category, count in counts.items():
                        summary_by_role[role][category] += count
                    entry["by_role"][role] = {
                        "summary": counts,
                        "changes": self.changes(target, role, show_all=show_all, limit=limit),
                    }
            targets.append(entry)
        declared_edges = self.declared_edges(scope) if include_edges else []
        edge_limit = None if show_all else limit
        report = {
            "format_version": 1, "comparison": "semantic", "scope": scope, "summary": summary,
            "targets_added": self.targets_added, "targets_removed": self.targets_removed,
            "targets": targets, "related_limit": limit,
            "declared_edge_count": len(declared_edges), "declared_edges": declared_edges[:edge_limit],
            "notes": [NOTE, "Targets requested on only one side are listed separately; their closures are not compared."],
        }
        if scope == "all":
            report["summary_by_role"] = summary_by_role
            nested = []
            if include_edges and self.targets:
                # The raw report without an explicit target can also cover
                # edges outside the comparable roots.
                edges = declared_edges if self._declared_targets is not None else self._raw_edges(self.targets)
                consumers = {edge["from"] for edge in edges} - set(self.targets)
                for consumer in sorted(consumers):
                    nested.extend(self.nested_closure_deltas(consumer, limit=edge_limit))
            report["nested_closure_count"] = len(nested)
            report["nested_closures"] = nested[:edge_limit]
        return report

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

    def declared_changes(self, target, scope="all"):
        """Changed edges in the target subgraph or selected closure."""
        if target not in self.targets:
            raise ValueError("Unknown semantic comparison target: " + target)
        result = []
        for edge in self.declared_edges(scope, target=target):
            before, after = edge["old"], edge["new"]
            if before and after:
                change = "retained" if before == after else "role_changed"
            else:
                change = "removed" if before else "added"
            result.append(dict(edge, change=change))
        return result

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
                if scope == "all":
                    before, after = (sorted(graph.edges.get((parent, child), ())) for graph in self.graphs)
                else:
                    before, after = ([scope] if used else [] for used in covered[scope])
                result.append({"from": parent, "to": child, "roles": relevant, "old": before, "new": after})
        return result


def has_semantic_changes(report):
    return bool(report["targets_added"] or report["targets_removed"]
                or any(report["summary"][c] for c in ("added", "removed", "role_changed")))


def declared_edge_label(edge):
    transition = role_transition(edge["old"], edge["new"])
    return f"{edge['from']} -> {edge['to']} [{transition}]"


def role_summary(role, counts):
    line = f"@{role}  +{counts['added']} -{counts['removed']} packages"
    if counts.get("retained"):
        line += f"  ·  routes changed: {counts['retained']}"
    return line


def render_semantic(report, *, color=False):
    def paint(text, category):
        code = {"added": "32", "removed": "31", "retained": "36"}.get(category)
        return f"\033[{code}m{text}\033[0m" if color and code else text

    def package_lines(view, suffix):
        result = []
        for category, mark in (("removed", "-"), ("added", "+"), ("retained", "="), ("unchanged", " ")):
            entries = [record for record in view["changes"] if record["change"] == category]
            if not entries:
                continue
            count = view["summary"][category]
            result.append(f"  {LABELS[category]} ({count}):")
            for record in entries:
                result.append(paint(f"    {mark} {record['name']}{suffix}", category))
            if len(entries) < count:
                result.append(f"    … {count - len(entries)} more. Use --format json.")
        return result

    scope = report["scope"]
    roles = ROLES if scope == "all" else (scope,)
    summaries = report["summary_by_role"] if scope == "all" else {scope: report["summary"]}
    lines = [role_summary(role, summaries[role]) for role in roles]
    for key, label in (("targets_added", "Targets only in new snapshot"),
                       ("targets_removed", "Targets only in old snapshot")):
        if report[key]:
            lines.append(label + ": " + ", ".join(report[key]))
    for target in report["targets"]:
        for role in roles:
            view = target["by_role"][role] if scope == "all" else target
            if not view["changes"]:
                continue
            suffix = f"@{role}" if scope == "all" else ""
            lines.append("\n" + target["target"] + suffix)
            lines.extend(package_lines(view, suffix))
    if not has_semantic_changes(report):
        lines.append("\nNo dependency membership or role changes at the selected roots.")
    if not report["targets"]:
        lines.append("No common requested targets. Closures cannot be compared.")
    if scope == "all" and report["declared_edge_count"]:
        count = report["declared_edge_count"]
        edges = report["declared_edges"]
        lines.append(f"\nDeclared edge changes ({count}):")
        lines.extend("  " + declared_edge_label(edge) for edge in edges)
        if len(edges) < count:
            lines.append(f"  … {count - len(edges)} more. Use --all, --format json or --tui.")
    if scope == "all" and report["nested_closure_count"]:
        count = report["nested_closure_count"]
        nested = report["nested_closures"]
        lines.append(f"\nNested consumer closure changes ({count}):")
        for view in nested:
            role = view["scope"]
            lines.append(f"\n{view['target']}{role_summary(role, view['summary'])}")
            lines.extend(package_lines(view, f"@{role}"))
        if len(nested) < count:
            lines.append(f"  … {count - len(nested)} more consumer scopes. Use --all or --format json.")
    return "\n".join(lines)
