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
"""Public CLI framing, element ownership and temporary checkout lifecycle."""

from pathlib import Path

import pytest

from bst_grep.project import Paths, Project, checkout_root


def framed(args, records):
    template = args[args.index("--format") + 1]
    return "".join(_substitute(template, record) for record in records)


def _substitute(template, record):
    for field, value in record.items():
        template = template.replace("%{" + field + "}", value)
    return template


def test_selection_options_and_multiline_config(tmp_path):
    calls = []

    def run(args):
        calls.append(args)
        if "%{config}" in args[args.index("--format") + 1]:
            return framed(
                args, [{"config": "build-commands:\n- echo 'hello'\n", "vars": "flags: -O2\n", "env": "{}\n"}]
            )
        return framed(args, [{"name": "sdk.bst:dir/app.bst", "workspace-dirs": ""}])

    project = Project("/project with spaces", [("arch", "aarch64")], run=run)
    assert project.select(["sdk.bst:dir/app.bst"], "build", workspaces=True) == {"sdk.bst:dir/app.bst": ""}
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
    assert calls[0][calls[0].index("--deps") + 1] == "build"
    with project.configuration("sdk.bst:dir/app.bst") as (root, origin):
        assert origin == "resolved"
        assert (root / "config.yaml").read_text() == "build-commands:\n- echo 'hello'\n"
        assert sorted(p.name for p in root.iterdir()) == ["config.yaml", "env.yaml", "vars.yaml"]
    assert not root.exists()


@pytest.mark.parametrize("response", ["unframed", "unsupported", "duplicate"])
def test_rejects_unsupported_or_malformed_show(response):
    def run(args):
        if response == "unframed":
            return "unexpected text"
        if response == "unsupported":
            return framed(args, [{"name": "%{name}"}])
        return framed(args, [{"name": "app.bst"}, {"name": "app.bst"}])

    with pytest.raises(ValueError):
        Project(run=run).select(["app.bst"], "none")


def test_checkout_strips_wrappers_but_keeps_single_source_directory():
    destinations = []

    def run(args):
        assert args[-2:] == ["--", "sdk.bst:dir/code.bst"]
        assert "--include-build-scripts" not in args
        destination = Path(args[args.index("--directory") + 1])
        destinations.append(destination)
        root = destination / "sdk" / "dir-code" / "src"
        root.mkdir(parents=True)
        (root / "main.c").write_text("needle\n")
        return ""

    with Project(run=run).sources("sdk.bst:dir/code.bst", "") as (root, origin):
        assert origin == "checkout"
        assert (root / "src" / "main.c").is_file()
    assert not destinations[0].parent.exists()


def test_checkout_cleanup_on_failure_and_interrupt():
    for exception in (ValueError("fetch failed"), KeyboardInterrupt()):
        destinations = []

        def run(args):
            destination = Path(args[args.index("--directory") + 1])
            destination.mkdir()
            destinations.append(destination)
            raise exception

        with pytest.raises(type(exception)):
            with Project(run=run).sources("code.bst", ""):
                pass
        assert not destinations[0].parent.exists()


def test_workspace_is_read_directly_and_never_removed(tmp_path):
    (tmp_path / "dirty.c").write_text("edited\n")
    project = Project(run=lambda args: pytest.fail("workspace must not trigger checkout"))
    with project.sources("app.bst", f"Workspace: {tmp_path}") as (root, origin):
        assert origin == "workspace"
        assert root == tmp_path.resolve()
    assert (tmp_path / "dirty.c").read_text() == "edited\n"
    with pytest.raises(ValueError, match="workspace-dirs"):
        with project.sources("app.bst", "not a workspace"):
            pass


def test_empty_and_unexpected_checkouts(tmp_path):
    assert checkout_root(tmp_path, "empty.bst") == tmp_path
    (tmp_path / "unexpected.txt").write_text("bad layout")
    with pytest.raises(ValueError, match="layout"):
        checkout_root(tmp_path, "app.bst")


def test_paths_cycles_shared_dependencies_and_multiple_roots():
    paths = Paths({"app": ["a", "b"], "a": ["lib"], "b": ["lib"], "lib": ["a"], "other": ["lib"]}, ["app", "other"])
    assert paths.to("lib") == [["app", "a", "lib"], ["other", "lib"]]
    assert paths.to("missing") == []


def test_paths_query_parses_public_dependency_lists():
    def run(args):
        fields = args[args.index("--format") + 1]
        if "%{build-deps}" not in fields:
            return framed(args, [{"name": "app.bst"}])
        return framed(
            args,
            [
                {"name": "app.bst", "build-deps": "- sdk.bst:lib.bst\n", "runtime-deps": "[]\n"},
                {"name": "sdk.bst:lib.bst", "build-deps": "[]\n", "runtime-deps": "[]\n"},
            ],
        )

    assert Project(run=run).paths(["app.bst"]).to("sdk.bst:lib.bst") == [["app.bst", "sdk.bst:lib.bst"]]


@pytest.mark.parametrize(
    "scope, expected",
    [
        ("all", [["app", "lib"]]),
        ("run", [["app", "runtime", "lib"]]),
        ("build", [["app", "lib"]]),
        ("none", []),
    ],
)
def test_why_respects_dependency_scope(scope, expected):
    def run(args):
        fields = args[args.index("--format") + 1]
        if "%{build-deps}" not in fields:
            return framed(args, [{"name": "app"}])
        return framed(
            args,
            [
                {"name": "app", "build-deps": "[lib]", "runtime-deps": "[runtime]"},
                {"name": "runtime", "build-deps": "[]", "runtime-deps": "[lib]"},
                {"name": "lib", "build-deps": "[]", "runtime-deps": "[]"},
            ],
        )

    assert Project(run=run).paths(["app"], scope).to("lib") == expected
