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
import copy
import random

import pytest

from bst_tree.model import Graph
from bst_tree.semantic import SemanticDiff, has_semantic_changes, render_semantic


def graph(edges, targets=("app",), extra=()):
    names = set(targets) | set(extra) | {n for e in edges for n in e}
    return Graph(list(targets), {n: {"kind": "manual", "key": "a", "source_info": None, "workspace": False}
                                 for n in names}, {e: frozenset(k) for e, k in edges.items()}, {"strict": True})


def changes(model, target="app", scope="all"):
    return {r["name"]: r for r in model.changes(target, scope)}


def test_removed_direct_dependency_stays_reachable_via_another_path():
    old = graph({("app", "lib"): ["run"], ("app", "mid"): ["run"], ("mid", "lib"): ["run"]})
    new = copy.deepcopy(old)
    del new.edges["app", "lib"]
    model = SemanticDiff(old, new)
    assert changes(model) == {"lib": {"name": "lib", "change": "retained", "old": ["run"], "new": ["run"]}}
    assert model.path(0, "app", "lib", "run") == ["app", "lib"]
    assert model.path(1, "app", "lib", "run") == ["app", "mid", "lib"]
    assert not has_semantic_changes(model.report())
    output = render_semantic(model.report())
    assert "Still reachable (1)" in output
    assert "= lib@run" in output
    assert "lib@run [" not in output
    assert model.route_changes("app", "lib")[0]["from"] == "app"


def test_lost_last_path_is_a_removal_even_when_node_is_still_in_loaded_graph():
    old = graph({("app", "mid"): ["run"], ("mid", "lib"): ["run"],
                 ("other", "lib"): ["run"]}, targets=("app", "other"))
    new = copy.deepcopy(old)
    del new.edges["mid", "lib"]
    model = SemanticDiff(old, new)
    assert changes(model)["lib"]["change"] == "removed"
    assert model.changes("other") == []
    assert has_semantic_changes(model.report())
    assert model.path(1, "app", "lib", "run") is None


def test_changed_longer_route_is_retained_even_when_shortest_witness_stays_identical():
    old = graph({("app", "lib"): ["run"], ("app", "mid"): ["run"], ("mid", "lib"): ["run"]})
    new = copy.deepcopy(old)
    del new.edges["mid", "lib"]
    model = SemanticDiff(old, new)
    assert changes(model)["lib"]["change"] == "retained"
    assert model.path(0, "app", "lib", "run") == model.path(1, "app", "lib", "run")
    assert model.route_changes("app", "lib")[0]["from"] == "mid"
    assert model.route_changes("app", "mid") == []


def test_roles_can_change_without_losing_the_element():
    old = graph({("app", "tool"): ["build"], ("tool", "lib"): ["run"]})
    new = copy.deepcopy(old)
    new.edges["app", "tool"] = frozenset(["run"])
    model = SemanticDiff(old, new)
    assert all(r["change"] == "role_changed" for r in model.changes("app"))
    assert changes(model)["lib"]["old"] == ["build"]
    assert changes(model)["lib"]["new"] == ["run"]
    assert changes(model, scope="run")["lib"]["change"] == "added"
    assert changes(model, scope="build")["lib"]["change"] == "removed"
    assert model.path(0, "app", "lib", "build") == ["app", "tool", "lib"]
    assert model.path(0, "app", "lib", "run") is None


def test_build_runtime_closure_excludes_build_of_build_and_runtime_build():
    old = graph({("app", "tool"): ["build"], ("tool", "lib"): ["run"],
                 ("tool", "bootstrap"): ["build"], ("app", "runtime"): ["run"],
                 ("runtime", "compiler"): ["build"]})
    new = copy.deepcopy(old)
    del new.edges["tool", "bootstrap"]
    del new.edges["runtime", "compiler"]
    assert SemanticDiff(old, new).changes("app") == []
    new.edges["tool", "lib"] = frozenset(["build"])
    assert changes(SemanticDiff(old, new)) == {"lib": {
        "name": "lib", "change": "removed", "old": ["build"], "new": [],
    }}


def test_extra_internal_build_type_does_not_change_runtime_routes():
    old = graph({("app", "mid"): ["run"], ("mid", "lib"): ["run"]})
    new = copy.deepcopy(old)
    new.edges["mid", "lib"] = frozenset(["run", "build"])
    assert not SemanticDiff(old, new).changes("app")


def test_combined_roles_and_alternative_build_route():
    old = graph({("app", "lib"): ["build", "run"], ("app", "tool"): ["build"], ("tool", "lib"): ["run"]})
    new = copy.deepcopy(old)
    new.edges["app", "lib"] = frozenset(["run"])
    assert changes(SemanticDiff(old, new))["lib"]["change"] == "retained"
    del new.edges["tool", "lib"]
    record = changes(SemanticDiff(old, new))["lib"]
    assert record == {"name": "lib", "change": "role_changed", "old": ["build", "run"], "new": ["run"]}


def test_runtime_only_changes_do_not_affect_build_scope():
    old = graph({("app", "tool"): ["build"], ("app", "lib"): ["run"]})
    new = copy.deepcopy(old)
    del new.edges["app", "lib"]
    model = SemanticDiff(old, new)
    assert model.changes("app", "build") == []
    assert changes(model, scope="run")["lib"]["change"] == "removed"


def test_added_and_removed_targets_are_coverage_changes_not_dependency_losses():
    old = graph({("app", "lib"): ["run"]}, targets=("app",))
    new = graph({}, targets=("lib",))
    report = SemanticDiff(old, new).report()
    assert report["targets"] == []
    assert report["targets_added"] == ["lib"]
    assert report["targets_removed"] == ["app"]
    assert report["summary"]["removed"] == 0
    assert has_semantic_changes(report)
    assert "No common requested targets" in render_semantic(report)
    with pytest.raises(ValueError, match="present in both"):
        SemanticDiff(old, new, targets=["app"])


def test_explicit_targets_ignore_other_target_selection_changes():
    old = graph({("app", "lib"): ["run"]}, targets=("app", "lib"))
    new = copy.deepcopy(old)
    new.targets = ["app"]
    report = SemanticDiff(old, new, ["app", "app"]).report()
    assert len(report["targets"]) == 1
    assert report["targets_removed"] == []
    assert not has_semantic_changes(report)


def test_attributes_do_not_affect_semantics_and_unchanged_is_opt_in():
    old = graph({("app", "lib"): ["run"]})
    new = copy.deepcopy(old)
    new.nodes["lib"].update(key="different", source_info=[{"version": "2"}])
    model = SemanticDiff(old, new)
    assert model.changes("app") == []
    assert model.changes("app", show_all=True)[0]["change"] == "unchanged"
    assert not has_semantic_changes(model.report())


def reference(graph, root, role):
    parents = {(root, role): None}
    pending = [(root, role)]
    while pending:
        parent, phase = pending.pop()
        for (p, child), kinds in graph.edges.items():
            state = child, "run"
            if p == parent and phase in kinds and state not in parents:
                parents[state] = (parent, phase)
                pending.append(state)
    return {name for name, phase in parents if name != root}


def test_roles_and_witnesses_match_reference_on_deep_cycles():
    rng = random.Random(17)
    names = ["app", "sub:lib", "b", "c", "d", "e"]
    for _ in range(25):
        edges = {(p, c): rng.choice([["run"], ["build"], ["run", "build"]])
                 for p in names for c in names if rng.random() < .25}
        old = graph(edges, extra=names)
        new = copy.deepcopy(old)
        model = SemanticDiff(old, new)
        for role in ("run", "build"):
            reached = reference(old, "app", role)
            assert {r["name"] for r in model.changes("app", role, show_all=True)} == reached
            for name in names:
                path = model.path(0, "app", name, role)
                assert bool(path) == (name in reached)
                if path:
                    assert path[0] == "app" and path[-1] == name
                    for i, (p, c) in enumerate(zip(path, path[1:])):
                        assert (role if i == 0 else "run") in old.edges[p, c]


def test_bounded_reports_keep_exact_counts_and_do_not_enumerate_paths():
    names = [f"n{i:04}" for i in range(1500)]
    old = graph({(p, c): ["run"] for p, c in zip(names, names[1:])}, targets=(names[0],))
    new = copy.deepcopy(old)
    del new.edges[names[0], names[1]]
    model = SemanticDiff(old, new)
    report = model.report(limit=5)
    assert report["summary"]["removed"] == 1499
    assert len(report["targets"][0]["changes"]) == 5
    assert not model._path_cache
    assert "1494 more" in render_semantic(report)
    path = model.path(0, names[0], names[-1], "run")
    assert len(path) == 1500


def test_invalid_scope_and_target_errors():
    old = graph({("app", "lib"): ["run"]})
    model = SemanticDiff(old, old)
    with pytest.raises(ValueError, match="scope"):
        model.report("missing")
    with pytest.raises(ValueError, match="target"):
        model.masks("missing")


def test_cli_semantic_check_ignores_retained_routes_and_cache_keys(tmp_path, capsys):
    import json
    from bst_tree.cli import main
    from bst_tree.model import write_snapshot

    old = graph({("app", "lib"): ["run"], ("app", "mid"): ["run"], ("mid", "lib"): ["run"]})
    new = copy.deepcopy(old)
    del new.edges["app", "lib"]
    new.nodes["app"]["key"] = "different"
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_snapshot(old, a)
    write_snapshot(new, b)
    assert main(["diff", str(a), str(b), "--semantic", "--check", "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["retained"] == 1
    assert main(["diff", str(a), str(b), "--check"]) == 1
    capsys.readouterr()
    del new.edges["mid", "lib"]
    write_snapshot(new, b)
    assert main(["diff", str(a), str(b), "--semantic", "--scope", "run", "--check"]) == 1
    output = capsys.readouterr().out
    assert "Removed (1)" in output
    assert "- lib" in output
    assert main(["diff", str(a), str(b), "--semantic", "--scope", "build", "--check"]) == 0


def test_cli_semantic_json_color_filters_and_validation(tmp_path, capsys):
    import json
    from bst_tree.cli import main
    from bst_tree.model import write_snapshot

    old = graph({("app", "lib"): ["run"]})
    new = copy.deepcopy(old)
    new.edges["app", "lib"] = frozenset(["build"])
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_snapshot(old, a)
    write_snapshot(new, b)
    assert main(["diff", str(a), str(b), "--semantic", "--structure-only", "--explain", "--format", "json", "--color", "always"]) == 0
    output = capsys.readouterr().out
    assert "\033" not in output
    report = json.loads(output)
    assert report["summary"]["role_changed"] == 1
    assert "explanation" in report
    assert main(["diff", str(a), str(b), "--semantic", "--color", "always"]) == 0
    output = capsys.readouterr().out
    assert "@build  +1 -0 packages" in output
    assert "@run  +0 -1 packages" in output
    assert "\033[32m    + lib@build" in output
    assert "\033[31m    - lib@run" in output
    assert report["summary_by_role"]["build"]["added"] == 1
    assert report["summary_by_role"]["run"]["removed"] == 1
    assert report["targets"][0]["by_role"]["run"]["changes"] == [
        {"name": "lib", "change": "removed", "old": ["run"], "new": []}
    ]
    args = ["diff", str(a), str(b), "--scope", "run", "--target", "app", "--check"]
    assert main(args) == 1
    assert "- lib" in capsys.readouterr().out
    assert main([*args, "--format", "json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["targets"][0]["changes"] == [{"name": "lib", "change": "removed", "old": ["run"], "new": []}]
    for flags in (["--scope", "run", "--reverse"], ["--target", "app"], ["--semantic", "--target", "missing"], ["--semantic", "--reverse"]):
        assert main(["diff", str(a), str(b), *flags]) == 2
        assert capsys.readouterr().err


def test_cli_runtime_diff_from_a_dependency_of_the_snapshot_root(tmp_path, capsys):
    from bst_tree.cli import main
    from bst_tree.model import write_snapshot

    shared = {("sdk.bst", "delivery.bst"): ["build"], ("delivery.bst", "runtime.bst"): ["run"]}
    old = graph({**shared, ("runtime.bst", "bootstrap.bst"): ["run"],
                 ("bootstrap.bst", "gcc.bst"): ["run"]}, targets=("sdk.bst",))
    new = graph({**shared, ("runtime.bst", "linux-base.bst"): ["run"],
                 ("runtime.bst", "linux-toolchain.bst"): ["build"],
                 ("linux-toolchain.bst", "gcc.bst"): ["run"]}, targets=("sdk.bst",))
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_snapshot(old, a)
    write_snapshot(new, b)

    args = ["diff", str(a), str(b), "--scope", "run", "--check"]
    assert main(args) == 0
    assert "No dependency membership or role changes" in capsys.readouterr().out
    assert main([*args, "--target", "delivery.bst"]) == 1
    output = capsys.readouterr().out
    assert "- bootstrap.bst" in output
    assert "- gcc.bst" in output
    assert "+ linux-base.bst" in output
    assert "linux-toolchain.bst" not in output
    assert "@run  +1 -2 packages" in output


def test_nested_consumer_loses_transitive_packages_while_root_retains_them():
    shared = {
        ("export.bst", "ghc.bst"): ["build"],
        ("export.bst", "keeper.bst"): ["build"],
        ("keeper.bst", "bootstrap.bst"): ["run"],
        ("keeper.bst", "linux-base.bst"): ["run"],
        ("bootstrap.bst", "gcc.bst"): ["run"],
        ("linux-base.bst", "libc.bst"): ["run"],
    }
    old = graph({
        **shared, ("ghc.bst", "bootstrap.bst"): ["build", "run"], ("unrelated.bst", "gcc.bst"): ["run"],
    }, targets=("export.bst",))
    new = graph(
        {**shared, ("ghc.bst", "bootstrap.bst"): ["build"], ("ghc.bst", "linux-base.bst"): ["run"]},
        targets=("export.bst",),
        extra=("unrelated.bst",),
    )
    model = SemanticDiff(old, new)
    report = model.report("all")
    assert not has_semantic_changes(report)
    assert report["nested_closure_count"] == 1
    view, = report["nested_closures"]
    assert (view["target"], view["scope"]) == ("ghc.bst", "run")
    assert view["summary"] == {"added": 2, "removed": 2}
    assert [(r["change"], r["name"]) for r in view["changes"]] == [
        ("removed", "bootstrap.bst"), ("removed", "gcc.bst"),
        ("added", "libc.bst"), ("added", "linux-base.bst"),
    ]
    output = render_semantic(report)
    assert "ghc.bst@run  +2 -2 packages" in output
    assert "- gcc.bst@run\n" in output
    assert "+ libc.bst@run\n" in output
    assert len(model.report("all", limit=1)["nested_closures"][0]["changes"]) == 2
    assert "nested_closures" not in model.report("run")
    assert not SemanticDiff(old, new, ["keeper.bst"]).report("all")["nested_closures"]
    assert not SemanticDiff(old, new, ["ghc.bst"]).report("all")["nested_closures"]


def test_scoped_diff_ignores_edges_outside_the_selected_closure(tmp_path, capsys):
    import json
    from bst_tree.cli import main
    from bst_tree.model import write_snapshot

    old = graph({
        ("sdk.bst", "delivery.bst"): ["build"],
        ("sdk.bst", "other.bst"): ["build"],
        ("delivery.bst", "toolchain.bst"): ["build", "run"],
        ("delivery.bst", "linux-base.bst"): ["build"],
        ("delivery.bst", "bootstrap.bst"): ["run"],
        ("other.bst", "bootstrap.bst"): ["build", "run"],
    }, targets=("sdk.bst",), extra=("new-tool.bst",))
    new = copy.deepcopy(old)
    new.edges["delivery.bst", "toolchain.bst"] = frozenset(["build"])
    new.edges["delivery.bst", "linux-base.bst"] = frozenset(["build", "run"])
    new.edges["delivery.bst", "new-tool.bst"] = frozenset(["build"])
    new.edges["other.bst", "bootstrap.bst"] = frozenset(["build"])
    del new.edges["delivery.bst", "bootstrap.bst"]
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_snapshot(old, a)
    write_snapshot(new, b)

    args = ["diff", str(a), str(b), "--scope", "run", "--check"]
    for layout in ("flat", "tree"):
        assert main([*args, "--format", layout]) == 0
        output = capsys.readouterr().out
        assert "No dependency membership or role changes" in output
        assert "@run  +0 -0 packages" in output
        assert "toolchain.bst" not in output
        assert "bootstrap.bst" not in output

    assert main([*args, "--target", "delivery.bst", "--format", "json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["declared_edges"] == [
        {"from": "delivery.bst", "to": "bootstrap.bst", "old": ["run"], "new": []},
        {"from": "delivery.bst", "to": "linux-base.bst", "old": [], "new": ["run"]},
        {"from": "delivery.bst", "to": "toolchain.bst", "old": ["run"], "new": []},
    ]
    model = SemanticDiff(old, new)
    assert model.report("run")["declared_edges"] == []
    build_report = model.report("build")
    assert build_report["summary"]["removed"] == 2
    assert build_report["summary"]["added"] == 1
    assert len(build_report["declared_edges"]) == 4
    assert all(
        set(edge["old"] + edge["new"]) == {"build"} for edge in build_report["declared_edges"]
    )
    assert model.report("all")["declared_edge_count"] == 5
    assert "Declared edge changes (5)" in render_semantic(model.report("all"))
    assert "Declared edge changes" not in render_semantic(build_report)
    assert len(model.report("all", limit=1)["declared_edges"]) == 1
    assert len(model.report("all", limit=1, show_all=True)["declared_edges"]) == 5

    for layout in ("flat", "tree"):
        assert main(["diff", str(a), str(b), "--format", layout, "--structure-only", "--explain"]) == 0
        output = capsys.readouterr().out
        assert "build/run -> build" in output
        assert "build -> build/run" in output
        assert "bootstrap.bst" in output
        assert "Change analysis:" in output


def test_role_effects_match_independent_traversal_after_random_edge_changes():
    rng = random.Random(37)
    names = ["app", "a", "b", "c", "d", "e"]
    for _ in range(25):
        def random_graph():
            return graph({(p, c): rng.choice([["run"], ["build"], ["run", "build"]])
                          for p in names for c in names if rng.random() < .2}, extra=names)
        old, new = random_graph(), random_graph()
        model = SemanticDiff(old, new)
        for scope in ("all", "run", "build"):
            roles = ("build", "run") if scope == "all" else (scope,)
            closure = [{r: reference(g, "app", r) for r in roles} for g in (old, new)]
            records = {r["name"]: r for r in model.changes("app", scope, show_all=True)}
            for name in names:
                before, after = ([r for r in roles if name in closure[side][r]] for side in (0, 1))
                if not before and not after:
                    assert name not in records
                    continue
                record = records[name]
                assert record["old"] == before and record["new"] == after
                if not before:
                    assert record["change"] == "added"
                elif not after:
                    assert record["change"] == "removed"
                elif before != after:
                    assert record["change"] == "role_changed"
                else:
                    assert record["change"] in ("retained", "unchanged")
                    assert bool(model.route_changes("app", name, scope)) == (record["change"] == "retained")
