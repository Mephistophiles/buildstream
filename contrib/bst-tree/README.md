# bst-tree

Explore BuildStream dependencies in a terminal and compare portable graph snapshots.
Requires Python 3.10+. BuildStream and the project's plugins must be installed and
`bst` available on PATH when reading a live project. Snapshot browsing and diffing
need neither BuildStream nor the original project.

```sh
python3 -m venv .venv-bst-tree
.venv-bst-tree/bin/pip install ./contrib/bst-tree
.venv-bst-tree/bin/bst-tree browse -C /path/to/project app.bst
.venv-bst-tree/bin/bst-tree snapshot -C /path/to/project app.bst -o before.json
# After changing the project:
.venv-bst-tree/bin/bst-tree snapshot -C /path/to/project app.bst -o after.json
.venv-bst-tree/bin/bst-tree diff before.json after.json
.venv-bst-tree/bin/bst-tree diff before.json after.json --format json --check
.venv-bst-tree/bin/bst-tree browse --snapshot before.json
```

Multiple targets and repeated `--option NAME VALUE` are supported. Snapshot writes
are atomic and replace an existing output file. Snapshots always contain the full
graph, even if you use a narrower scope in the UI.

## Navigation

Arrow keys or `hjkl` navigate; Enter/Space toggle a branch. `/` searches all element
names, including collapsed branches; `n`/`N` visit matches. `s` cycles all/run/build
scope. Build scope includes direct build dependencies and their runtime closure,
with the target retained as a visual root. `r` shows reverse dependencies of the
selected element, Escape restores the previous tree, `w` shows one shortest path
from each applicable target, and `q` exits (cancelling a pending load).

Children are materialized only when expanded. Shared dependencies may be explored
under multiple parents. Reverse dependencies and paths refer to the selected scope.

## Comparison

`diff` is always noninteractive. `+`, `-`, and `~` mark additions, removals, and
changes. Edge annotations show old/new build/run types. Unchanged ancestors provide
context; `--all` includes unchanged branches. Shared subgraphs are printed once,
with later occurrences linking to the earlier node. Counts refer to unique nodes
and edges. Renames appear as removal plus addition. Metadata differences are
reported separately. Colors are emitted only to a terminal.

Normal success exits 0. With `--check`, differences (including metadata) exit 1.
Errors exit 2. JSON output has its own `format_version: 1`, a summary, node and edge
changes with `old`/`new`, and metadata changes.

## Data and limitations

This tool uses documented `bst show` fields, not private BuildStream APIs. The
installed BuildStream must support `name`, `kind`, `full-key`, `source-info`,
`workspaced`, `build-deps`, and `runtime-deps`. Unsupported output fails explicitly.
No build, track or fetch command is run. Project loading can still require junction
sources and plugin configuration; cache queries may follow BuildStream settings.

Source comparison uses plugin-provided `source-info`; plugins do not necessarily
expose exact refs. Empty provenance is stored as null (unavailable), which also
covers elements without sources. Unresolved keys are null. A key change alone is
not interpreted as an upstream version change. Workspace content is not captured;
only workspace presence is recorded. Artifact contents and sizes are not compared.

Snapshots use `format_version: 1`, canonical targets, metadata (bst version, project
options and strict mode), a map of nodes keyed by full junction-qualified name,
and typed directed edges. There are no timestamps or absolute project paths.
Unknown format versions and dangling edges are rejected. Treat snapshots as project
data: source provenance may contain URLs and other plugin-provided information.

## Development

```sh
python -m pip install -e './contrib/bst-tree[test]'
python -m pytest -c contrib/bst-tree/pyproject.toml contrib/bst-tree/tests
# Include live integration tests when bst and buildbox-casd are available:
BST_TREE_TEST_LIVE=1 python -m pytest -c contrib/bst-tree/pyproject.toml contrib/bst-tree/tests
```
