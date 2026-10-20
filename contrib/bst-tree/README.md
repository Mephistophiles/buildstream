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
| `diff OLD NEW --tui` | Inspect changes and dependency candidates interactively | A terminal; no `bst` required |

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
selected element with its direct consumers expanded and the element selected.
Deeper branches remain lazy; `r` again or Escape restores the previous tree, `w` shows one shortest path
from each applicable target, and `q` exits (cancelling a pending load).

Reverse dependencies are limited to the graph loaded from the requested targets
and the current scope. Opening `lib.bst` alone loads its dependencies, not every
consumer elsewhere in the project. Include the relevant top-level targets when
launching the browser (or creating a snapshot) to include those consumers. An
empty reverse view explicitly reports that no consumers exist in the loaded
scope.

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
  temporary `bst source checkout --deps none --include-build-scripts` and browse it.
  The checkout includes generated build scripts; these are not executed. The source view
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
Dotfiles and hidden directories are included in the source browser, for both
workspaces and temporary checkouts. Symlinks remain excluded from that browser.
Workspace contents reflect current local edits, not necessarily the built artifact.
Each reopened viewer queries the live project again using the same directory,
options, and strict mode as graph loading. Snapshot browsing keeps these actions
unavailable because snapshots do not contain configuration or files.

## Comparison

`diff` defaults to noninteractive tree output. `--tui` (or `--format tui`) opens
the interactive change inspector described below. `+`, `-`, and `~` mark additions, removals, and
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
whether a target lost all transitive paths to a dependency; use `--semantic`
for that comparison. `--reverse` only changes
tree presentation; JSON edge directions, counts, and `--check` are unchanged.

By default all fields are compared, including cache keys. To focus on a dependency
reorganization or specific attributes:

```sh
bst-tree diff before.json after.json --structure-only
bst-tree diff before.json after.json --ignore-fields key
bst-tree diff before.json after.json --fields kind source_info workspace
```

`--fields` selects attributes and `--ignore-fields` excludes attributes. Available
fields are `kind`, `key` (cache key), `source_info`, `workspace`, `details`
(resolved inputs), and `metadata` (snapshot metadata). These options and `--structure-only` are mutually exclusive.
Node additions/removals, dependency edges and their build/run types, and targets
are always compared. `--structure-only` skips all attributes and snapshot metadata.
Filtering applies to tree and JSON output, counts, and `--check`; nodes changed
only in ignored fields disappear from the diff. JSON node `old`/`new` objects
contain only selected attributes (empty objects in structure-only mode).
`--all` can be combined with any filter to show unchanged branches as context.

Normal success exits 0. With `--check`, differences in the selected comparison exit 1.
Errors exit 2. JSON output has its own `format_version: 1`, a summary, node and edge
changes with `old`/`new`, and metadata changes.

## Semantic dependency comparison

```sh
bst-tree diff before.json after.json --semantic
bst-tree diff before.json after.json --semantic --scope run --check
bst-tree diff before.json after.json --semantic --target app.bst --format json
bst-tree diff before.json after.json --semantic --tui
```

This compares **reachable elements relative to each common requested target**,
using either basic or detailed snapshots. No new snapshot fields or BuildStream
installation are required. `--scope all` (the default) compares both roles:

- **run**: the target's transitive runtime dependencies;
- **build**: its direct build dependencies plus their transitive runtime dependencies.

A dependency can have both roles. Build dependencies of build dependencies, and
build dependencies of runtime dependencies, are not automatically part of these
closures. The target itself is always excluded, including in cyclic snapshots.

The report separates **added**, **removed**, **role changed**, and **still reachable**.
For example, removing `app -> lib` while retaining `app -> mid -> lib` keeps `lib`
in the runtime closure. It appears as still reachable, rather than removed. If
`lib` changes from runtime to build-only use, it appears as a role change. A role
loss can also be shown as a removal by selecting only that scope.

Still-reachable entries have unchanged role membership but a relevant declared
route changed. They include changes to longer routes even when one shortest
witness stays identical. Unrelated edge changes and internal build-edge changes
that do not affect either selected role are omitted. Unchanged dependencies are
included only with `--all`. Per-target counts are exact; aggregate counts count
**target/dependency pairs**, so a shared dependency can contribute to several targets.

`--check` in semantic mode exits 1 for membership or role changes, or changes to
the requested target set. A route-only reorganization exits 0. Attribute, source,
cache-key and snapshot-metadata differences do not affect this status. Use the
ordinary structural diff when declared edge changes should also fail a check.
`--fields`, `--ignore-fields` and `--structure-only` continue to apply to optional
`--explain` analysis, but do not change graph reachability. `--reverse` is rejected
in semantic mode.

Targets requested on only one side are listed separately and their closures are
not compared: omitting a target from a capture is not evidence that its libraries
were removed. Repeated `--target TARGET` limits comparison to targets requested
in both snapshots and ignores other target-selection differences. With no common
targets, the report explicitly states that there is no comparable closure.

Text output shows at most ten entries per category and target, with exact total
counts. JSON is a complete standalone report (`comparison: "semantic"`,
`format_version: 1`) containing the selected scope, aggregate summary, per-target
summaries and `changes` with role lists in `old` and `new`, plus unmatched targets.
It does not enumerate all paths. `--explain` can add the cache-key evidence report.
Colors follow the usual `--color` and `NO_COLOR` rules.

In the TUI, press **m** from the input-change inspector to open reachability, or
start there with `--semantic --tui`. Choose a target with **t**, a scope with **s**,
and filter dependency names with **/**. **1** shows effects, **2** still-reachable
entries, and **0** all listed entries. The initial view focuses on effects, or
on still-reachable entries when there are no membership/role changes. Use **f**
to inspect full witness paths, the changed routes relevant to a dependency, or
snapshot context. Overview paths abbreviate long chains; the full path section
does not. One deterministic shortest witness is found per role and side, on demand;
cycles are bounded and all possible paths are never enumerated. Changed-route
lists show up to 100 entries with exact counts. Escape returns to the input-change
inspector, preserving its selection; **q** exits. The original CLI mode determines
`--check` on exit, even if the other view was opened interactively.

Reachability describes declared elements, not the final staged file set or a
prediction that a build will run. The comparison is exact for the loaded snapshot
graphs and does not search for consumers outside them.

## Interactive change analysis

Capture resolved inputs **before and after** the change:

```sh
bst-tree snapshot -C /path/to/project app.bst --details -o before.json
# Change source refs, element configuration, or project settings.
bst-tree snapshot -C /path/to/project app.bst --details -o after.json
bst-tree diff before.json after.json --tui
# For scripts / CI:
bst-tree diff before.json after.json --explain --format json --check
```

`--details` adds effective `config`, `vars`, `env`, and `public` mappings to the
same framed `bst show --deps all` query that captures the graph. No build, track,
source checkout or artifact export is run. As with ordinary graph capture,
project loading can require junction sources and project plugins. The two
snapshots should use the same targets, options and BuildStream version.
Detailed snapshots may be substantially larger and can contain environment values,
URLs or credentials present in project configuration. Treat them as project data.

The inspector initially focuses on elements with **observed local changes** when
there are any. The header always reports the total changed elements and category
counts. The left pane lists each element once; the right pane shows a short
summary and one small diff. Long diffs are abbreviated only in the overview. Related-element lists show up
to 100 entries with exact total counts; JSON explanation exports the full lists.
Observed local origins are listed before intermediary keys.
Use the section picker to view the complete selected section. Cache-key hashes
are available in their own section, rather than filling the overview.

- `0` / `1` / `2` / `3`: all changes / observed inputs / dependency candidates /
  unexplained changes. Added and removed elements appear under All.
- `/`: filter names, including junction-qualified names; Escape returns to the list.
- Arrows or `j`/`k`: select an element. Tab switches focus between controls.
- `f`: select a changed section, dependency candidates, changed consumers, or context.
- `c`: toggle three lines versus full diff context in a selected section.
- `m`: inspect per-target semantic dependency effects; Escape returns to input changes.
- `q`: exit. `--check` retains the same exit semantics after closing the inspector.

`--all` also lists unchanged elements. Existing `--fields`, `--ignore-fields` and
`--structure-only` filters apply to the comparison and analysis. Snapshot context
remains available for interpreting results. `--reverse` is a tree-output option
and is rejected with the TUI. The inspector adapts to narrow terminals by stacking
the element list above the detail pane. Identical snapshots and context-only
changes have an explicit empty state with access to snapshot context.

The categories describe **evidence, not proven causes**:

- **Observed inputs:** captured source information, kind, workspace presence,
  resolved settings, or outgoing dependency declarations changed. Settings can
  change without changing the cache key, for example a nocache environment value.
- **Dependency candidates:** the selected element's resolved key changed, and a
  build input's resolved key or build-closure membership changed. Both snapshots
  must confirm strict mode. Build inputs mean direct build dependencies plus
  their runtime closure; a runtime-only dependency of the element is not sufficient.
- **Unexplained:** no supported candidate or observed local change was found.
  Unresolved keys and unconfirmed strict mode never receive dependency attribution.
- **Added / removed:** an element exists on only one side.

Changed consumers are the changed elements that list an element as a dependency
candidate; they are not a prediction that a build will run. An origin with a changed key but no captured local evidence remains in the
unexplained category, even when it is a dependency candidate for another element. No private
BuildStream key reconstruction is attempted: plugin unique keys, sandbox settings,
project fatal-warning configuration, and environment nocache rules are not fully
captured. Workspace file contents are not captured. Version and option differences
are called out in snapshot context.

Old snapshots remain useful for graph/key/source comparisons. If either snapshot
lacks details, resolved-input comparison is skipped and the inspector reports the
coverage gap. Missing details are not treated as an input removal, and the gap
alone does not trigger `--check`. Use `--explain` for a bounded text analysis or add
it to JSON output for the full evidence records. The default noninteractive output
and JSON diff format remain compatible; the extra `explanation` object is opt-in.
Snapshot and noninteractive diff commands still do not import Textual.

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

Basic snapshots use `format_version: 1`; `snapshot --details` writes version 2
with a separate, validated per-element `details` map. Both versions are readable.
Snapshots contain canonical targets, metadata (bst version, project
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
(`diff.py`), change evidence (`explain.py`, with compact build-input reachability
in `keygraph.py`), target-relative semantic comparison (`semantic.py`), and
terminal navigation (`tui.py`, `diff_tui.py`, and `semantic_tui.py`). Element inspection lives in
`inspection.py` and `inspection_tui.py`. Snapshot and noninteractive diff commands
do not import Textual. Each interactive inspection owns a cancellable command runner and its
temporary exports; late background results cannot update a closed viewer.

Tests cover parsing, snapshots, comparison filters, scoped graphs, cycles,
headless navigation, inspection, cancellation, and temporary-file cleanup.
Live tests exercise a real project and artifact exports. They use an explicit
Linux/aarch64 sandbox target without executing target binaries. On macOS,
`buildbox-casd` may require a higher file-descriptor limit such as
`ulimit -n 4096`; projects may also need an explicit `sandbox.build-arch`.
