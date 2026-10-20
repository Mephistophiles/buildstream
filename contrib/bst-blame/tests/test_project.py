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
import pytest

from bst_blame.project import Graph, Project
from helpers import framed, node


@pytest.mark.parametrize(
    "scope, expected",
    [
        ("all", [["root", "lib"]]),
        ("run", [["root", "runtime", "lib"]]),
        ("build", [["root", "lib"]]),
        ("none", []),
    ],
)
def test_scoped_shortest_paths_with_cycles(scope, expected):
    nodes = {"root": node(build=["lib"], run=["runtime"]), "runtime": node(run=["lib"]), "lib": node(run=["runtime"])}
    assert Graph(nodes, ["root"], set(nodes), scope).paths("lib") == expected


def test_public_show_queries_and_options():
    calls = []

    def run(args):
        calls.append(args)
        fields = args[args.index("--format") + 1]
        if "%{kind}" not in fields:
            return framed(args, [{"name": "app.bst"}])
        return framed(
            args,
            [
                {
                    "name": "app.bst",
                    "kind": "import",
                    "full-key": "a" * 64,
                    "state": "cached",
                    "build-deps": "[]",
                    "runtime-deps": "[]",
                }
            ],
        )

    project = Project("/project with spaces", [("arch", "aarch64")], run=run)
    graph = project.graph(["app.bst"], "all")
    assert graph.selected == {"app.bst"}
    assert graph.paths("app.bst") == [["app.bst"]]
    assert calls[0][:8] == [
        "bst",
        "--no-colors",
        "--strict",
        "-C",
        "/project with spaces",
        "--option",
        "arch",
        "aarch64",
    ]
    for command in project.commands("sdk.bst:app.bst").values():
        assert command[-2:] == ["--", "sdk.bst:app.bst"]
        assert command[:8] == calls[0][:8]
    assert "--no-integrate" in project.checkout_command("app.bst")
    assert project.checkout_command("app.bst")[-5:] == ["--no-integrate", "--tar", "-", "--", "app.bst"]


@pytest.mark.parametrize("response", ["unexpected", "unsupported", "duplicate"])
def test_malformed_output(response):
    def run(args):
        if response == "unexpected":
            return "unexpected"
        return framed(args, [{"name": "%{name}"}] if response == "unsupported" else [{"name": "a"}, {"name": "a"}])

    with pytest.raises(ValueError):
        Project(run=run).names(["a"], "none")


def test_list_queries_never_pull_or_checkout():
    calls = []
    project = Project(run=lambda args: calls.append(args) or "")
    project.listings(["a.bst", "sdk.bst:lib.bst"])
    assert len(calls) == 2
    assert all(call[3:5] == ["artifact", "list-contents"] for call in calls)
    assert not any("pull" in call or "checkout" in call or "build" in call for call in calls)
