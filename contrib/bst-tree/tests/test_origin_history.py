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
"""Exercise the actual Cython composition hooks, not reconstructed YAML guesses."""

import pytest

pytest.importorskip("buildstream")
from buildstream import node as nodes

pytestmark = pytest.mark.skipif(
    not hasattr(nodes, "_set_composition_observer"), reason="Requires rebuilt fork node extension"
)


def load(tmp_path, name, text):
    from buildstream import _yaml

    path = tmp_path / name
    path.write_text(text)
    return _yaml.load(str(path), name)


def report(node, history, section="vars"):
    from buildstream._input_provenance import build_report

    result = build_report("app", {section: node.clone()}, {section: node.strip_node_info()}, history=history)
    return {entry["path"]: entry for entry in result["entries"]}


def test_real_override_order_includes_condition_and_clone(tmp_path):
    from buildstream._composition_history import capture_history
    from buildstream._options.optionpool import OptionPool

    with capture_history(True) as history:
        base = load(tmp_path, "project.conf", "variables:\n  optimization: -O2\n")
        include = load(tmp_path, "flags.yml", "variables:\n  optimization: -O1\n")
        element = load(
            tmp_path,
            "app.bst",
            "variables:\n  optimization: -O3\n(?):\n- debug:\n    variables:\n      optimization: -O0\n",
        )
        include._composite_under(element)
        pool = OptionPool(str(tmp_path))
        pool.load(nodes.Node.from_dict({"debug": {"type": "bool", "description": "Debug", "default": True}}))
        pool.resolve()
        pool.process_node(element)
        element.get_mapping("variables")._composite(base.get_mapping("variables"))
        entry = report(base.get_mapping("variables"), history)["/vars/optimization"]
        assert [step["raw"] for step in entry["history"]] == ["-O2", "-O1", "-O3", "-O0"]
        assert [step["state"] for step in entry["history"]] == ["overridden"] * 3 + ["effective"]
        assert [step["origin"]["file"].rsplit("/", 1)[-1] for step in entry["history"]] == [
            "project.conf",
            "flags.yml",
            "app.bst",
            "app.bst",
        ]
        assert entry["history"][-1]["conditions"][0]["expression"] == "debug"
        assert entry["history"][-1]["conditions"][0]["origin"]["line"] == 4
        assert entry["raw"] == "-O0"


def test_list_append_prepend_reset_and_deferred_directives(tmp_path):
    from buildstream._composition_history import capture_history

    with capture_history(True) as history:
        base = load(tmp_path, "base.yml", "commands: [base]\n")
        for name, text in [
            ("append.yml", "commands:\n  (>): [tail]\n"),
            ("prepend.yml", "commands:\n  (<): [head]\n"),
            ("reset.yml", "commands:\n  (=): [reset]\n"),
        ]:
            load(tmp_path, name, text)._composite(base)
        entry = report(base, history, "config")["/config/commands"]
        assert entry["raw"] == ["reset"]
        assert [step["state"] for step in entry["history"]] == ["overridden"] * 3 + ["contributes"]
        assert entry["history"][-1]["raw"] == {"(=)": ["reset"]}
        first = load(tmp_path, "first.yml", "commands:\n  (>): [one]\n")
        load(tmp_path, "second.yml", "commands:\n  (>): [two]\n")._composite(first)
        target = nodes.Node.from_dict({"commands": []})
        first._composite(target)
        entry = report(target, history, "config")["/config/commands"]
        assert entry["raw"] == ["one", "two"]
        assert [step["raw"] for step in entry["history"]] == [[], {"(>)": ["one"]}, {"(>)": ["two"]}]


def test_shared_source_does_not_mix_element_histories_and_generated_locations(tmp_path):
    from buildstream._composition_history import capture_history

    with capture_history(True) as history:
        source = load(tmp_path, "override.yml", "flags: -O3\n")
        one = load(tmp_path, "one.yml", "flags: -O1\n")
        two = load(tmp_path, "two.yml", "flags: -O2\n")
        source._composite(one)
        source._composite(two)
        assert [s["raw"] for s in report(one, history)["/vars/flags"]["history"]] == ["-O1", "-O3"]
        assert [s["raw"] for s in report(two, history)["/vars/flags"]["history"]] == ["-O2", "-O3"]
        one["flags"] = "generated"
        entry = report(one, history)["/vars/flags"]
        assert entry["origin"]["synthetic"] and entry["origin"]["file"] is None
        assert entry["history"][-1]["operation"] == "generated"


def test_capture_releases_nodes_and_restores_observer_on_exception():
    from buildstream._composition_history import capture_history

    with pytest.raises(RuntimeError):
        with capture_history(True) as history:
            base = nodes.Node.from_dict({"flag": "before"})
            nodes.Node.from_dict({"flag": "after"})._composite(base)
            assert history.lineage
            raise RuntimeError("cancel query")
    assert not history.lineage and not history.conditions and not history.generated_nodes
    assert nodes._set_composition_observer(None) is None
