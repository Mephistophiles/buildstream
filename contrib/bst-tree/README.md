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
| `artifact-diff OLD NEW` | Compare artifact files, metadata, ABI and nested formats | Working `bst`, or two local tar archives with `--archives`; analyzers optional |
| `tree TARGET...` | Print dependencies, optionally filtering names with `--match` | Working `bst`, or a saved graph with `--snapshot` |
| `rdeps ELEMENT TARGET...` | Print direct or transitive consumers in the loaded graph | Working `bst`, or a saved graph with `--snapshot` |
| `paths ELEMENT TARGET...` | Print one shortest path per target to an element | Working `bst`, or a saved graph with `--snapshot` |
| `inspect ELEMENT` | Read effective configuration, source provenance or artifact files | Working `bst` and project plugins |

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

Snapshot writes are atomic and **replace an existing output file**; `-o -` writes
JSON to stdout instead. Snapshots
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

## Artifact content comparison

Compare two immutable artifact refs (`project/normalized-element/full-key`), two
cached element names, or a ref and an element. An element name resolves against
the current project, so use refs to retain an older revision. Both checkouts use
the same project directory and repeated `--option NAME VALUE` settings.

```sh
bst-tree artifact-diff myproject/app/OLD_FULL_KEY app.bst -C /project --tui
bst-tree artifact-diff before.tar after.tar --archives --tui
bst-tree artifact-diff before.tar after.tar --archives --path etc/config --format json
bst-tree artifact-diff before.tar after.tar --archives --abidiff --diffoscope --check
bst-tree artifact-diff before.tar after.tar --archives --diffoscope-html report.html
```

Live comparison exports each artifact with `bst artifact checkout --deps none
--no-integrate --tar …`. It never builds or runs integration commands; checkout
may retrieve missing data from configured artifact remotes. Both archives consume
temporary disk space until the command closes. `--archives` reads two existing
checkout tar archives without BuildStream or the original project; it cannot be
combined with project arguments. Graph snapshots do not contain artifact files
and are not inputs to this command.

The initial comparison streams SHA-256 over the complete contents of regular
files and compares type, permission bits, numeric uid/gid, link targets and device
numbers. It reports additions, removals and modifications. Timestamps, extended
attributes, textual owner names and the archive's root directory entry are ignored.
Renames appear as removal/addition. Hardlinks are resolved only to regular members
inside the archive; symlinks and special files are never extracted or followed.
Unsafe paths, duplicate entries and invalid hardlink chains are rejected.

Only changes are listed by default. `--all` also lists unchanged entries;
repeatable `--path` selects **exact relative paths**, including unchanged paths,
without glob expansion. Text previews show a unified diff with three context lines
and at most 256 KiB per side; full-content hashes remain authoritative. Binary
files are identified instead of decoded. CLI summaries are abbreviated; JSON and
the TUI retain the captured report up to the configured limits.

In the artifact TUI, the file list and detail pane stay visible together (stacked
on narrow terminals). **All / Content / Metadata / ELF** filters and `/` path
search narrow the list. `a` runs **abidiff** for the selected paired ELF file;
`d` runs **diffoscope** for the selected paired regular file, and `Shift+D` runs
it on both whole archives, including additions/removals and nested formats. Use
`f` to choose overview, text, metadata or an analyzer report; Tab changes focus,
arrow keys or `j`/`k` navigate/scroll, and `q` or Escape closes and cancels pending
work. Escape in search returns focus to the file list. Tool errors remain visible
and the action can be retried. Analyses run only when requested by a shortcut or
CLI flag, so opening a large artifact does not automatically launch every analyzer.

### Optional analyzers

Install `abidiff` and `diffoscope` on PATH independently of bst-tree. For example,
Debian/Ubuntu provide `abigail-tools` and `diffoscope-minimal`; additional diffoscope
comparators improve the detail available for particular formats. A missing requested
tool is an explicit analysis error; inventory and text comparison work without them.

`--abidiff` analyzes changed files that are ELF on both sides; explicit `--path`
also permits unchanged ELF pairs. Added/removed ELF files and type changes remain
inventory changes. The
[abidiff exit status](https://sourceware.org/libabigail/manual/abidiff.html)
is decoded as a bitmask: 0 means no reported change, 4 means change requiring
review, and 12 means an incompatible change was reported. Other statuses are
errors. **Exit 4 does not prove compatibility**, and no reported ABI change does
not guarantee a complete comparison. Type analysis depends on debug information;
stripped binaries can provide only their available ELF interfaces. Provide separate
host debug roots with repeated `--debug-info-dir-old DIR` and
`--debug-info-dir-new DIR`. Embedded separate debug files are not automatically
extracted alongside a selected binary. Host default suppression files are disabled;
explicit `--abidiff-suppression FILE` options are recorded in the report.

`--diffoscope` compares the whole archives, or each selected `--path` file pair.
For added/removed files, omit `--path` to analyze the whole archives. The
[diffoscope statuses](https://manpages.debian.org/unstable/diffoscope-minimal/diffoscope.1.en.html)
0/1/other mean no reported difference/difference/error. `--diffoscope-html FILE`
implies diffoscope, accepts at most one selected path, and atomically replaces
that file only after a successful analysis (including an empty report for equal
inputs). HTML output can show deeper nested format differences than the summary.

`--timeout SECONDS` bounds each checkout/analyzer command (default 120 seconds).
Timeout and cancellation terminate the owned subprocess group. Selected files are
materialized only when required, privately and without executable permission.
`--report-limit BYTES` bounds captured output per stream and diffoscope report
sizes (default 1 MiB); external comparators and report limits can affect detail.
Reports distinguish tool failure, timeout, unavailable tools and reported changes.
Normal success exits 0; `--check` exits 1 for selected inventory or analyzer
changes. Requested analysis errors exit 2 even without `--check`; cancellation
exits 130. `--check` measures all selected file changes, not ABI compatibility alone.

## Headless queries and inspection

`tree`, `rdeps`, `paths` and `inspect` do not require a TTY or import Textual.
Their default is plain text; `--format json` produces one JSON document on stdout.
BuildStream diagnostics and errors go to stderr. Exit statuses are 0 for a
successful query (including an empty result), 2 for errors and 130 for cancellation.
Existing `diff` and `artifact-diff` also support headless text/JSON and `--check`.

```sh
bst-tree tree app.bst sdk.bst -C /project --scope build
bst-tree tree app.bst -C /project --match llvm --format json
bst-tree rdeps lib.bst app.bst sdk.bst -C /project --format json
bst-tree rdeps lib.bst app.bst -C /project --direct
bst-tree paths lib.bst app.bst -C /project --scope run --format json

# Offline queries need neither bst nor the original project:
bst-tree tree --snapshot graph.json
bst-tree rdeps lib.bst --snapshot graph.json --format json
bst-tree paths lib.bst --snapshot graph.json
bst-tree snapshot app.bst -C /project -o - | bst-tree tree --snapshot - --format json
```

Live queries require explicit graph roots (`TARGET...`), with repeated
`--option NAME VALUE` and strict mode. Offline `--snapshot FILE` replaces those
roots and cannot be combined with live targets, `-C` or project options. `-` reads
stdin. An `ELEMENT` query uses its exact name in the loaded graph, including any
junction qualification. An unknown element is an error. An element excluded by
`--scope` produces an empty result with `in_scope: false` in JSON.

Scopes match the TUI: `all` includes the complete loaded graph; `run` follows
runtime dependencies; `build` includes each root's direct build dependencies and
their runtime closure, retaining the roots themselves. `rdeps` is limited to the
selected roots' loaded graph and scope; it does not discover consumers elsewhere
in the project. Its default is transitive; `--direct` includes only immediate
consumers and edges to the queried element. `paths` returns one shortest path per
reachable root, with deterministic choices when several paths are equally short.
It does not enumerate every possible route. `tree --match TEXT` searches names
case-insensitively and retains matching nodes plus ancestor context. Shared
subtrees and cycles are expanded once in text output.

Query JSON uses its own `format_version: 1` and `query` field. `tree` and `rdeps`
include `roots` and a flat `graph` object (`nodes`, `edges`, metadata and any saved
details); the edge direction remains **consumer → dependency**, even for `rdeps`.
`rdeps.consumers` excludes the queried element itself. `paths.paths` contains
`target`, `nodes` and typed `edges` for each witness. Empty query graphs are valid
results, not snapshots to pass back to `browse --snapshot`. Use `snapshot` to
produce a complete portable graph.

```sh
bst-tree inspect app.bst -C /project --format json
bst-tree inspect app.bst -C /project --section sources --format json
bst-tree inspect app.bst -C /project --section sources --files --path .config
bst-tree inspect app.bst -C /project --section sources --checkout --files --path app/main.c
bst-tree inspect app.bst -C /project --section artifacts --path usr/share/app.conf --format json
bst-tree inspect app.bst -C /project --section artifacts --pull --format json
```

`inspect --section build` (the default) returns structured effective `config`,
`vars`, `env` and `public` data, without executing build commands. `sources`
returns provenance and workspace location. `--files` lists a workspace, including
hidden files; symlinks are listed with targets but not traversed. `--path` previews
an exact relative regular-file path without following links. Without an open
workspace, request `--checkout` explicitly to create a temporary source checkout
with build scripts. It may fetch sources; scripts are not executed, and the
checkout is removed after success, error or cancellation. `--checkout` also lets
you request a temporary checkout when a workspace is open; JSON identifies the
chosen origin without presenting a removed temporary path as a persistent workspace.
Paths are relative to the listed tree. A temporary checkout preserves BuildStream's
layout: sources live under the normalized element directory (for example,
`app/main.c`) and generated build scripts live at the checkout root. Run `--files`
first to discover exact paths, especially for junction-qualified elements.

`artifacts` lists the selected artifact's paths and long metadata, with no
automatic pull. `--pull` explicitly downloads only this element without its
dependencies before listing. `--path` exports a temporary artifact tar for preview
without integration or builds; checkout may retrieve missing data through
configured artifact remotes. Archive links and special files are not followed.
The temporary archive is removed when inspection finishes. `--checkout` is valid
only for sources, and `--pull` only for artifacts.

Inspection JSON also uses `format_version: 1`, with `query: "inspect"`, `element`
and `section`. Preview objects contain `path`, `text` (null for binary content),
`binary`, `truncated` and `limit_bytes`; truncation never needs to be inferred
from human-readable text. `--limit BYTES` defaults to 256 KiB. Source listings have
structured type/mode/size fields; artifact listings preserve BuildStream's long
metadata in `details`, alongside `path` and `directory`.

All four commands accept `--timeout SECONDS` (default 120 per bst invocation)
and `--output-limit BYTES` (default 32 MiB per captured stream). Overflow is an
error rather than a silently partial graph or inspection. Cancellation and timeout
terminate the owned subprocess group. These limits do not cap the size of a saved
graph read from disk or stdin, or the number of source files listed locally.

## Artifact size and binary growth

```sh
bst-tree size product.bst sdk.bst -C /project --scope run --format json
bst-tree size product.bst -C /project --check-max 100000000
bst-tree size --archive app.bst app.tar --archive runtime.bst runtime.tar
bst-tree size --snapshot graph.json --archive app.bst app.tar --archive runtime.bst runtime.tar
bst-tree artifact-diff old.tar new.tar --archives --bloaty --path usr/lib/libapp.so
bst-tree artifact-diff old.tar new.tar --archives --bloaty --bloaty-sources sections,compileunits --format json
```

`size` reads each selected artifact once, hashing regular contents in a temporary
checkout tar without integration or builds. Live checkout may retrieve missing
data from configured artifact remotes. Optional direct CAS mode uses the shared
persistent index instead of exporting tar files (see below).
`--scope` defaults to `run` and matches the graph browser, retaining roots in build
scope. Offline archives can be independent roots, or associated with exact element
names in a saved graph. Missing archives/failed exports are explicitly incomplete;
unknown totals are null, with separate `known_file_bytes` partial counts.

Each element reports its own logical regular/hardlink file bytes and the closure
inside the selected visible graph. Shared dependency elements are counted once
per closure and once in the total. For each requested root, `exclusive` excludes
elements also reachable from another requested root. `content_unique_bytes`
deduplicates full-file SHA-256 content, including identical files in different
artifacts. This is not CAS-chunk/compression usage, reclaimable disk space, the
size of an integrated/staged rootfs, or filesystem block usage. Directories,
symlink bodies and archive headers are excluded. These distinctions also appear
in the versioned JSON report. `--check-max BYTES` exits 1 only for a complete total
above the budget; incomplete/error exits 2. Normal success exits 0.

`artifact-diff --bloaty` profiles changed paired ELF files, or explicit selected
ELF pairs. Install the optional `bloaty` executable on PATH. Text, JSON and TUI
reports use the same analyzer; in the artifact TUI press **b** for Bloaty. The
report compares new against old, with at most 30 rows per level, and distinguishes
file size from ELF VM size (which is not process RSS). `--bloaty-sources` accepts
comma-separated `sections`, `symbols`, `segments` and `compileunits`; defaults to
`sections,symbols`. Symbol/compile-unit attribution depends on symbols/debug info
available in the selected binary; separate debug files are not automatically
extracted. Bloaty exit 0 means `analyzed`, not that sizes are unchanged; nonzero,
missing executables and timeouts are errors. Inventory hashes and `--check` remain
the authoritative selected-file change check. Existing timeout/cancellation and
captured-output bounds apply; no target binary is executed.

## Configuration provenance

```sh
# Requires the bst executable from this fork, including its provenance extension:
bst-tree origin app.bst -C /project --path /env/CFLAGS
bst-tree origin app.bst -C /project --path /config/build-commands --format json
bst-tree origin app.bst -C /project --option debug True --path /vars/flags
bst show --deps none --provenance app.bst > origins.json
bst-tree origin --from-report origins.json --path /env/CFLAGS --format json
```

`origin` follows a value to its winning composed YAML location, then follows
`%{variable}` references transitively through the variable definitions. Pointers
use RFC 6901 escaping (`~1` for `/`, `~0` for `~`); a pointer can select a whole
subtree such as `/config/build-commands`. Multiple `--path` options are supported.
`--no-follow` shows only the selected entries; default selection includes all
entries. `--from-report` works without BuildStream, and an optional element name
selects one element from a multi-element report. It cannot be combined with live
project arguments. Missing requested fields are errors rather than empty evidence.

The fork's new `bst show --provenance` is the backend JSON interface. It supports
normal dependency selection, ordering, exclusions, project options and junctions,
and is incompatible with `--format`. It captures raw composed nodes only during
explicit provenance queries, before variable expansion, so normal builds and
existing `bst show` output do not pay the cloning cost. This is a small core
extension because the ordinary resolved `bst show` YAML has already lost source
locations; the frontend does not guess origins by searching `.bst` files. With an
unextended bst executable, the command reports a clear unsupported-backend error.
Install this fork as the environment's BuildStream (with normal BuildStream
runtime/buildbox prerequisites); updating bst-tree alone does not update bst.

Every report has `format_version: 1`, `query: "input-provenance"` and `elements`.
Entries contain the RFC 6901 `path`, unexpanded composed `raw`, resolved `value`,
`value_available`, `origin` (file, display, 1-based line/column, synthetic marker)
and variable `references`. The wrapper adds requested paths, selection/evidence
markers and any unavailable references. Generated/synthetic nodes have null
file/line/column; unavailable resolved values are explicitly marked. Reports
include effective environment/configuration values and should be handled like the
project's other configuration dumps.

The evidence describes **winning composed values**, not a full history of every
assignment: overwritten definitions and inactive conditional branches are not
reconstructed. Raw values preserve pre-variable-expansion content after YAML
composition, not source quoting or original file formatting. Variable links are
expansion evidence, not proof that a compiler executed those flags, nor a trace
of transformations inside element plugins. Project includes may require junction
source retrieval during loading; no artifact checkout, build, source checkout or
integration commands are requested by the provenance query.

All output is headless text or JSON. Normal success exits 0; invalid inputs,
unsupported backends and unavailable referenced evidence exit 2, interruption 130.
The existing per-command timeout and output bounds apply. Native backend tests
cover composed includes, overrides, conditional options, references and unchanged
ordinary `bst show`; CI tests the fork's pure Python changes against the released
BuildStream compiled runtime on Python 3.10/3.14.


## Shared CAS index

Install the optional [bst-cas](../bst-cas/README.md) project in this tool's
environment and point it at the actual local BuildStream CAS:

```sh
python -m pip install ./contrib/bst-cas ./contrib/bst-tree
export BST_CAS_DIRECTORY="${XDG_CACHE_HOME:-$HOME/.cache}/buildstream/cas"
bst-tree size product.bst --format json
```

`--cas-directory DIR` also enables CAS mode; `--cas-index FILE` or
`BST_CAS_INDEX` chooses the shared SQLite database. Its default location is
`XDG_CACHE_HOME/bst-cas/index.sqlite3`. Trees are reused by artifact files digest;
current owners come from the live project. Missing or garbage-collected objects
remain explicit errors/incomplete coverage. CAS mode does not pull artifacts,
run integration or modify the BuildStream cache.

CAS mode currently applies to `size`. It uses logical file sizes and SHA-256 CAS
digests for content uniqueness; it does not measure compressed or reclaimable
CAS disk space. `coverage.cas_index` includes reuse statistics. Offline archive
inputs and artifact comparison continue to use their existing archive readers.
