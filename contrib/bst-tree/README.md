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

Select an element and press `m` for its action menu. Highlighted `a` / `b` / `c`
keys and the menu footer show available actions; ↑/↓ and Enter also select an
action. You can use these shortcuts directly from the graph:

- `a`: browse the locally cached artifact's file tree with ↑/↓ and ←/→.
  The selected file's full path, permissions, type, size, and symlink target are
  shown in the adjacent pane (`bst artifact list-contents`, plain and `--long`).
  Missing artifacts produce an error in the viewer; no build or artifact pull
  is started. This view displays file metadata, not file contents.
- `b`: show the resolved element configuration, including build commands where
  supported by its kind, plus variables and environment (`bst show`). These are
  the effective settings, rather than the original `.bst` YAML or a build log.
- `c`: show source provenance and browse files directly in an existing workspace.
  Without a workspace, press `f` or choose **Load source files** to create a
  temporary `bst source checkout --deps none` and browse it. The source view
  explains the operation and shows its command with the project's options.
  BuildStream uses its source cache and fetches missing sources from configured
  remotes or upstream. The temporary checkout is removed on close, cancellation,
  or error; fetched sources remain in BuildStream's cache.

The viewers load on demand without blocking navigation back to the tree. Escape
or `q` closes a viewer and cancels its pending command, preserving tree expansion
and selection. Use Tab/Shift+Tab to switch between files and the text pane;
↑/↓ select files, ←/→ close/open directories, and Enter previews a source file.
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
No build or track command is run. Source checkout runs only when requested with
`f` or **Load source files**, and can fetch missing sources. Project loading can
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

## Development

### Architecture before element inspection (2026-10-02)

The original implementation has five layers:

- `cli.py` parses `browse`, `snapshot`, and `diff`. Only `browse` imports
  Textual. `CancellableRunner` owns a single `bst` subprocess, captures its
  diagnostics, and terminates it when the application exits.
- `adapter.py` calls the public `bst show` CLI three times: version, the full
  graph, and canonical target names. Random record/field delimiters frame
  multiline YAML. It records kind, cache key, source provenance, workspace
  presence, and build/run edges; it does not load element files or contents.
- `model.py` owns the UI-independent `Graph`, scoped graphs, shortest paths,
  validation, and atomic version-1 JSON snapshot writes. A node is keyed by
  its full junction-qualified name; an edge carries build/run types.
- `diff.py` compares snapshots and renders text/JSON, with attribute filters,
  reverse edges, and bounded rendering of shared subtrees and cycles.
- `tui.py` owns a Textual `Explorer`: header, search input, dependency `Tree`,
  JSON details `Static`, status, and footer. Graph loading runs in a thread.
  Each UI occurrence stores its ancestor path, allowing shared dependencies
  to appear repeatedly. Children are populated on expansion. Scope and reverse
  mode rebuild the tree; reverse mode saves expanded paths and selection.

Original menu/navigation weaknesses to retain as regression cases:

- All actions live at application level alongside the tree's own bindings;
  focus and shortcut routing must be handled when adding other views.
- The footer lists every navigation shortcut and has no compact action menu;
  terminal resize is tested, but action visibility at small sizes is not.
- Selecting the synthetic root leaves the previous element's details visible.
- Escape in the search input also leaves reverse mode, instead of just closing
  search. Reverse/search restoration and queued expansion events share mutable
  tree state and need care around rebuilds.

Existing tests cover graph parsing/snapshots/diffs, headless TUI navigation,
lazy expansion, cycles, and loading cancellation. Live CLI coverage is opt-in.

### Element inspection extension

`inspection.py` provides `ProjectInspector` and bounded workspace file previews.
It uses the public CLI and has no dependency on Textual or private cache layouts.
`inspection_tui.py` contains the element menu and an isolated inspection screen
with a read-only text viewer, lazy source/workspace directory tree, and selectable
artifact file tree. Temporary source checkouts use the public CLI rather than
private CAS paths or mounts. Each screen owns its cancellable command runner;
background results cannot update a closed screen. The checkout worker retains
ownership of its temporary directory until the command finishes, so closing a
screen cannot remove a directory while BuildStream is still writing it.
`cli.py` supplies a factory for live projects, while snapshot browsing supplies
none. The graph model and snapshot format are unchanged.

The extension also clears stale root details, closes search before leaving reverse
mode on Escape, and prevents graph shortcuts from firing inside inspection views
while retaining Tab/Shift+Tab focus navigation. Queued expansion and highlight
events from previous tree roots are ignored after a rebuild.
The footer hides redundant navigation bindings and exposes the scrollable element
menu. Tests cover these focus/return paths, a small terminal, command arguments,
unavailable artifacts/workspaces, bounded file previews, and queued scope changes.

```sh
python -m pip install -e './contrib/bst-tree[test]'
python -m pytest -c contrib/bst-tree/pyproject.toml contrib/bst-tree/tests
# Include live integration tests when bst and buildbox-casd are available:
BST_TREE_TEST_LIVE=1 python -m pytest -c contrib/bst-tree/pyproject.toml contrib/bst-tree/tests
```

Verified locally with BuildStream 2.8.0, Python 3.14, and Textual 8.2 on macOS
ARM64: all 62 tests pass, including live graph/snapshot comparison, resolved build
configuration, workspace file inspection, a missing artifact, and contents of a
real artifact built from an `import` element.

```sh
source .venv-bst-tree/bin/activate
python -m pip install 'buildstream==2.8.0' -e './contrib/bst-tree[test]'
# macOS: buildbox-casd is supplied by Homebrew's recc package.
brew install recc
ulimit -n 4096
BST_TREE_TEST_LIVE=1 python -m pytest -c contrib/bst-tree/pyproject.toml contrib/bst-tree/tests
```

The live test fixes its sandbox target to Linux/aarch64 and does not execute target
binaries. BuildStream 2.8.0 does not recognize Darwin's `arm64` host spelling when
deriving a default sandbox architecture; projects on this host need an explicit
`sandbox.build-arch` such as `aarch64`. The higher file descriptor limit is needed
by `buildbox-casd`; macOS's default of 256 was insufficient in this verification.
