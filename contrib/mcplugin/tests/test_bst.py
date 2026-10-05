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
"""Exercise the bundled graph adapter without bst-tree or BuildStream imports."""

from types import SimpleNamespace

import pytest

from bst_mc._bst import FIELDS, capture, parse_report


def report(marker, build="[]"):
    values = ["app.bst", "manual", "?" * 64,
              "- kind: git\n  extra-data:\n    note: |\n      multi\n      line", "", build, "[]"]
    return marker + "record\n" + "".join(
        marker + field + "\n" + value + "\n" for field, value in zip(FIELDS, values)
    ) + marker + "end\n"


def test_multiline_provenance():
    nodes, edges = parse_report(report("MARKER_", "- app.bst"), "MARKER_")
    assert nodes["app.bst"]["source_info"][0]["extra-data"]["note"] == "multi\nline\n"
    assert nodes["app.bst"]["key"] is None
    assert edges == {("app.bst", "app.bst"): frozenset({"build"})}


@pytest.mark.parametrize("bad", ["not a report", report("M_").replace("manual", "%{kind}"),
                                 report("M_", "invalid-dependencies"), report("M_") + "trailing junk"])
def test_rejects_unsupported_and_malformed_report(bad):
    with pytest.raises(ValueError):
        parse_report(bad, "M_")


@pytest.mark.parametrize("dependency", ["app.bst", "missing.bst"])
def test_capture_validates_edges_and_preserves_options(dependency):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "--version" in args:
            output = "bst 2.test\n"
        elif "all" in args:
            marker = args[args.index("--format") + 1].split("record\n")[0]
            output = report(marker, f"- {dependency}")
        else:
            output = "app.bst\n"
        return SimpleNamespace(returncode=0, stdout=output)

    if dependency == "missing.bst":
        with pytest.raises(ValueError, match="missing node"):
            capture(["app.bst"], "/project", [("arch", "aarch64")], run=run)
    else:
        graph = capture(["app.bst"], "/project", [("arch", "aarch64")], run=run)
        assert graph.targets == ["app.bst"]
        assert graph.metadata["options"] == {"arch": "aarch64"}
    for args in calls:
        assert args[:7] == ["bst", "--no-colors", "--strict", "-C", "/project", "--option", "arch"]
        assert args[7] == "aarch64"
