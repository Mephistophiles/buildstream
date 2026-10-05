# bst-tree

Explore BuildStream dependencies in a terminal and compare portable graph snapshots.
Requires Python 3.10+. BuildStream and the project's plugins must be installed and
`bst` available on PATH when reading a live project. Snapshot browsing and diffing
need neither BuildStream nor the original project.

For Midnight Commander panels and live project bookmarks, see
[`bst-mc`](../mcplugin/README.md). The two tools install independently.

## Installation and quick start

From the repository root:

```sh
pipx install ./contrib/bst-tree
bst-tree browse -C /path/to/project app.bst
```

If the command is not found, run `pipx ensurepath` and open a new terminal.
Install project plugins into the environment that provides `bst`, not bst-tree's
isolated environment. Activate that environment first if needed.
To update from a changed checkout, run `pipx install --force ./contrib/bst-tree`.

## Commands

| Command | Purpose | Requirements |
| --- | --- | --- |
| `browse -C PROJECT TARGET...` | Explore a live graph and inspect elements | Working `bst`, project plugins, and a terminal |
| `browse --snapshot FILE` | Explore a saved graph | A terminal; no original project or `bst` required |
| `snapshot -C PROJECT TARGET... -o FILE` | Save the full graph as JSON | Working `bst` and project plugins; no terminal required |
| `diff OLD NEW` | Compare saved graphs as text or JSON | Neither `bst` nor a terminal required |

Live `browse` and `snapshot` require at least one explicit target. `-C` defaults
to the current directory. Multiple targets, junction-qualified names, and repeated
`--option NAME VALUE` are supported; live queries run in strict mode.
`browse --snapshot` cannot be combined with targets, `-C`, or project options.

```sh
bst-tree browse -C /path/to/project --option arch x86-64 app.bst sdk.bst:tools.bst
bst-tree snapshot -C /path/to/project app.bst -o before.json
# After changing the project:
bst-tree snapshot -C /path/to/project app.bst -o after.json
bst-tree diff before.json after.json
bst-tree diff before.json after.json --format json --check
bst-tree browse --snapshot before.json
```

Snapshot writes are atomic and **replace an existing output file**. Snapshots
always contain the full graph; changing the UI scope does not modify the snapshot.
Use `bst-tree --help` or `bst-tree COMMAND --help` for argument descriptions.

## Navigation

Arrow keys or `hjkl` navigate; Enter/Space toggle a branch. `/` searches all element
names, including collapsed branches; `n`/`N` visit matches. `Shift+S` cycles all/run/build
scope. Build scope includes direct build dependencies and their runtime closure,
with the target retained as a visual root. `r` shows reverse dependencies of the
selected element, Escape restores the previous tree, `w` shows one shortest path
from each applicable target, and `q` exits (cancelling a pending load).

Children are materialized only when expanded. Shared dependencies may be explored
under multiple parents. Reverse dependencies and paths refer to the selected scope.

Select an element and press `m` for its action menu. Highlighted `a` / `b` / `s`
keys and the menu footer show available actions; ↑/↓ and Enter also select an
action. You can use these shortcuts directly from the graph:

- `a`: browse the locally cached artifact's file tree with arrow keys or `hjkl`.
  Folders, file icons, highlighting, and sorting match the source browser.
  The selected file's full path, permissions, type, size, and symlink target are
  shown in the adjacent pane (`bst artifact list-contents`, plain and `--long`).
  Press Enter on a regular file to preview its contents in the adjacent pane.
  The first preview exports the artifact to a temporary tar archive using
  `bst artifact checkout --deps none --no-integrate --tar …`; further previews
  reuse that archive until the viewer closes. Files are read directly from the
  archive without extracting them. The initial export can take time and disk
  space for large artifacts; closing the viewer cancels it and removes the archive.
  Listing requires a locally cached artifact. Checkout may retrieve missing data
  through configured artifact remotes; no build or integration commands are run.
  Symlinks display their targets and are not followed for previews.
  Press `p` or **Pull artifact from remotes** to download the selected element
  with `bst artifact pull --deps none`, using the same project and options.
  This action also works when the initial listing reports a missing artifact.
  On success the file tree is reloaded and any old preview archive is discarded.
  Errors are shown in the viewer and can be retried; Escape cancels the pull.
  Only the selected element is pulled, without its dependencies; downloaded data
  remains in BuildStream's cache after closing the viewer.
- `b`: show the resolved element configuration, including build commands where
  supported by its kind, plus variables and environment (`bst show`). These are
  the effective settings, rather than the original `.bst` YAML or a build log.
- `s`: show source provenance and browse files directly in an existing workspace.
  Without a workspace, press `f` or choose **Load source files** to create a
  temporary `bst source checkout --deps none` and browse it. The source view
  explains the operation and shows its command with the project's options.
  BuildStream uses its source cache and fetches missing sources from configured
  remotes or upstream. The temporary checkout is removed on close, cancellation,
  or error; fetched sources remain in BuildStream's cache.

The viewers load on demand without blocking navigation back to the tree. Escape
or `q` closes a viewer and cancels its pending command, preserving tree expansion
and selection. Use Tab/Shift+Tab to switch between files and the text pane;
↑/↓ or `j`/`k` select files, ←/→ or `h`/`l` close/open directories, and Enter
previews a source or artifact file.
Page Up/Down scroll the focused widget. Press `i` to restore the source provenance
or full artifact listing after inspecting a file. Text is read-only, previews
are limited to 256 KiB, binary files are identified, and symlinks are not browsed.
Workspace contents reflect current local edits, not necessarily the built artifact.
Each reopened viewer queries the live project again using the same directory,
options, and strict mode as graph loading. Snapshot browsing keeps these actions
unavailable because snapshots do not contain configuration or files.

## Comparison

`diff` is always noninteractive. `+`, `-`, and `~` mark additions, removals, and
changes. Edge annotations show old/new build/run types. Only changed branches and
their ancestor context are shown; unchanged paths to the dependency of a changed
edge are omitted. Filtering attributes also removes branches whose only changes
were in ignored fields. `--all` includes unchanged branches. Shared leaves repeat
their local changes without a reference. Shared subtrees are expanded once, with
later occurrences naming the shared node and the exact output line containing its
expansion. This keeps large shared graphs and cycles bounded. Counts refer to unique nodes
and edges. Renames appear as removal plus addition. Metadata differences are
reported separately. `--color auto` (the default) enables colors in a terminal,
unless `NO_COLOR` is set. Use `--color always` to preserve colors through a pipe
(for example, `less -R`) or `--color never` to disable them. Additions are green,
removals red, modifications yellow; old/new attribute values are red/green.
JSON output never contains color escapes.

The default direction is target -> dependencies: use it to see what changed inside
a target or compiler. `--reverse` renders dependency -> consumers, useful for
seeing where a dependency was removed:

```sh
bst-tree diff before.json after.json --structure-only --reverse
```

For example, removing `compiler.bst -> lib.bst` while keeping `app.bst -> lib.bst`
produces this reverse tree (the unchanged direct use by `app.bst` is omitted):

```text
Nodes: +0 -0 ~0; edges: +0 -1 ~0
Reverse dependencies (dependency -> consumers):
  lib.bst
`--   compiler.bst [- run -> none]
    `--   app.bst [build]
```

Here the edge annotation on `compiler.bst` describes its dependency on `lib.bst`.
This shows changed declared relationships and context, not a calculation of
whether a target lost all transitive paths to a dependency. `--reverse` only changes
tree presentation; JSON edge directions, counts, and `--check` are unchanged.

By default all fields are compared, including cache keys. To focus on a dependency
reorganization or specific attributes:

```sh
bst-tree diff before.json after.json --structure-only
bst-tree diff before.json after.json --ignore-fields key
bst-tree diff before.json after.json --fields kind source_info workspace
```

`--fields` selects attributes and `--ignore-fields` excludes attributes. Available
fields are `kind`, `key` (cache key), `source_info`, `workspace`, and `metadata`
(snapshot metadata). These options and `--structure-only` are mutually exclusive.
Node additions/removals, dependency edges and their build/run types, and targets
are always compared. `--structure-only` skips all attributes and snapshot metadata.
Filtering applies to tree and JSON output, counts, and `--check`; nodes changed
only in ignored fields disappear from the diff. JSON node `old`/`new` objects
contain only selected attributes (empty objects in structure-only mode).
`--all` can be combined with any filter to show unchanged branches as context.

Normal success exits 0. With `--check`, differences in the selected comparison exit 1.
Errors exit 2. JSON output has its own `format_version: 1`, a summary, node and edge
changes with `old`/`new`, and metadata changes.

## Data and limitations

This tool uses documented `bst show` fields, not private BuildStream APIs. The
installed BuildStream must support `name`, `kind`, `full-key`, `source-info`,
`workspaced`, `build-deps`, and `runtime-deps`. Unsupported output fails explicitly.
No build or track command is run. Explicit artifact pull runs on `p` in the
artifact viewer and downloads from configured artifact remotes. Source checkout
runs only when requested with `f` or **Load source files**, and can fetch missing sources. Project loading can
still require junction sources and plugin configuration; cache queries may follow
BuildStream settings.

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

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `bst` is missing or a live project fails to load | Check `command -v bst` and `bst -C /path/to/project show app.bst` in the same terminal; verify project plugins and options. |
| `browse requires a terminal` | Run it directly in a terminal; use `snapshot` and `diff` for scripts and CI. |
| Element actions are unavailable | Snapshot mode contains graph data only. Reopen the live project to inspect files or build configuration. |
| An artifact is missing | Open the artifact viewer and press `p` to pull it from configured remotes; the viewer does not build it. |
| Source files are not shown | Use an existing workspace or press `f` in the source viewer to request a temporary checkout. |
| A diff contains many cache-key changes | Use `--ignore-fields key` or `--structure-only`, according to the comparison you need. |

## Development

From the repository root, in a development virtualenv:

```sh
python -m pip install -e './contrib/bst-tree[test]'
python -m pytest -c contrib/bst-tree/pyproject.toml contrib/bst-tree/tests
# Opt in only with a working bst and buildbox-casd installation:
BST_TREE_TEST_LIVE=1 python -m pytest -c contrib/bst-tree/pyproject.toml contrib/bst-tree/tests
```

The implementation separates CLI parsing (`cli.py`), public BuildStream CLI
queries (`adapter.py`), graph validation and snapshots (`model.py`), comparison
(`diff.py`), and terminal navigation (`tui.py`). Element inspection lives in
`inspection.py` and `inspection_tui.py`. Snapshot and diff commands do not import
Textual. Each interactive inspection owns a cancellable command runner and its
temporary exports; late background results cannot update a closed viewer.

Tests cover parsing, snapshots, comparison filters, scoped graphs, cycles,
headless navigation, inspection, cancellation, and temporary-file cleanup.
Live tests exercise a real project and artifact exports. They use an explicit
Linux/aarch64 sandbox target without executing target binaries. On macOS,
`buildbox-casd` may require a higher file-descriptor limit such as
`ulimit -n 4096`; projects may also need an explicit `sandbox.build-arch`.
