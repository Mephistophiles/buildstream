# bst-tree

Explore BuildStream dependencies in a terminal and compare portable graph snapshots.
Requires Python 3.10+. BuildStream and the project's plugins must be installed and
`bst` available on PATH when reading a live project. Snapshot browsing and diffing
need neither BuildStream nor the original project.

For Midnight Commander panels and live project bookmarks, see
[`bst-mc`](../mcplugin/README.md). The two tools install independently.

## Installation and quick start

For the complete suite through pipx, run `./contrib/install-bst-tools.sh`
from the repository root. Rerun with `--force` after updating the checkout;
use `--headless` to omit TUIs on a fresh install. Add `--provenance` to install
the private backend for live YAML provenance and override history. See the
[pipx installation guide](../bst-tools-core/README.md#install-from-this-checkout)
for the shared environment and manual commands.

For only bst-tree, in your chosen Python environment:

```sh
python -m pip install ./contrib/bst-tools-core './contrib/bst-tree[tui]'
bst-tree browse -C /path/to/project app.bst
```

For headless commands, install `./contrib/bst-tree` without `[tui]`; Textual is
then neither installed nor imported. Install BuildStream project plugins into
the environment providing `bst`, which may be separate. Re-run the same install
command after updating the checkout. See the [tool suite](../bst-tools-core/README.md)
for installing all commands together.

## Commands

Installing the `bst-tree` package puts these executables on PATH.

| Executable | Purpose |
| --- | --- |
| `bst-tree browse` / `bst-tree tree` | Browse or print dependencies from a project or snapshot |
| `bst-snapshot` | Capture a portable JSON dependency graph and optional inputs |
| `bst-graph-diff` | Compare element attributes, declared edges and targets |
| `bst-closure-diff` | Compare dependency reachability and build/runtime roles per target |
| `bst-key-explain` | Report observed input changes, dependency candidates and captured provenance |
| `bst-rdeps` | Find direct or transitive consumers in the loaded graph |
| `bst-path` / `bst-paths` | Find a shortest path between two elements or one per requested target |
| `bst-chains` | Enumerate simple dependency chains between two elements |
| `bst-rebuild-select` | Select and order elements on paths from a target to a changed element |
| `bst-inputs` | Print effective configuration, variables, environment and public data |
| `bst-source-files` | Inspect source metadata, workspace files or a temporary checkout |
| `bst-artifact-files` | List or preview an element's artifact files |
| `bst-origin` | Query YAML input provenance and override history |
| `bst-du` | Measure artifact files and dependency closures |

Each executable has its own `--help`. Existing `bst-tree` subcommand invocations
remain available for compatibility. See the [migration roadmap](roadmap.md).

Live `browse` and `bst-snapshot` require explicit targets. `-C` defaults to the
current directory. Targets can be junction-qualified and live queries run in
strict mode. `browse --snapshot` cannot be combined with targets, `-C`, or
project options.

```sh
bst-tree browse -C /path/to/project --option arch x86-64 app.bst sdk.bst:tools.bst
bst-snapshot -C /path/to/project app.bst -o before.json
# After changing the project:
bst-snapshot -C /path/to/project app.bst -o after.json
bst-graph-diff before.json after.json
bst-closure-diff before.json after.json --scope run --format json --check
bst-tree browse --snapshot before.json
```

`bst-snapshot` writes JSON to stdout by default. `-o FILE` atomically replaces the
output file. The legacy `bst-tree snapshot` form still requires `-o FILE` or
`-o -`. Each headless comparison accepts `-` in place of one snapshot file.

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

`bst-graph-diff` defaults to `--format flat`. Each changed element gets one line, sorted by
its full name. `+`, `-`, and `~` mark additions, removals, and modifications.
Each dependency change appears on the line for the element that declares it,
with its build/run types. If several elements have the same old and new values
for the same parameter path, their lines reference one shared entry such as `@1`.
Resolved `config`, `vars`, `env`, and `public` changes are shown by parameter path
when both snapshots contain details. `--all` includes unchanged elements.

```text
Packages: +1 -1 ~2 | edges: +1 -1 ~0
~ app.bst | deps: + new-lib.bst [run], - old-lib.bst [run] | @1
~ compiler.bst | @1
+ new-lib.bst
- old-lib.bst

Shared parameter changes:
  @1 env/CFLAGS: "-O2" -> "-O3"
```

Renames appear as removal plus addition. Target and snapshot metadata changes
appear above the package list. Run `bst-key-explain` for input and cache-key analysis.
`--color auto` (the default) enables terminal colors unless `NO_COLOR` is set.
Use `--color always` to preserve colors through a pipe or `--color never` to
disable them. `--tui` opens the interactive change inspector described below.

`--format tree` shows paths from targets to dependencies, including the ancestors
of changed elements and edges. Add `--all` to include unchanged branches.
`--reverse` selects tree output by default and shows dependencies followed by
their consumers:

```sh
bst-graph-diff before.json after.json --structure-only --reverse
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
whether a target lost all transitive paths to a dependency. Use `bst-closure-diff`
for that comparison. `--reverse` only changes
tree presentation; JSON edge directions, counts, and `--check` are unchanged.

By default all fields are compared, including cache keys. To focus on a dependency
reorganization or specific attributes:

```sh
bst-graph-diff before.json after.json --structure-only
bst-graph-diff before.json after.json --ignore-fields key
bst-graph-diff before.json after.json --fields kind source_info workspace
```

`--fields` selects attributes and `--ignore-fields` excludes attributes. Available
fields are `kind`, `key` (cache key), `source_info`, `workspace`, `details`
(resolved inputs), and `metadata` (snapshot metadata). These options and `--structure-only` are mutually exclusive.
Node additions/removals, dependency edges and their build/run types, and targets
are always compared. `--structure-only` skips all attributes and snapshot metadata.
Filtering applies to flat, tree, and JSON output, counts, and `--check`. Nodes changed
only in ignored fields disappear from the diff. JSON node `old`/`new` objects
contain only selected attributes (empty objects in structure-only mode).
`--all` can be combined with any filter to show unchanged elements as context.

Normal success exits 0. With `--check`, differences in the selected comparison exit 1.
Errors exit 2. JSON output has its own `format_version: 1`, a summary, node and edge
changes with `old`/`new`, and metadata changes.

## Semantic dependency comparison

```sh
bst-closure-diff before.json after.json
bst-closure-diff before.json after.json --scope run --check
bst-closure-diff before.json after.json --target app.bst --format json
bst-closure-diff before.json after.json --tui
```

With `--scope run`, a dependency that moves from runtime to build-only use
appears under **Removed**. With `--scope all`, the same change appears under
**Role changed** if it remains reachable in the build closure. The legacy `bst-tree diff --scope run`
form selects the same runtime comparison.

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
Use `bst-graph-diff` or `bst-key-explain` for field selection.

Targets requested on only one side are listed separately and their closures are
not compared: omitting a target from a capture is not evidence that its libraries
were removed. Repeated `--target TARGET` limits comparison to targets requested
in both snapshots and ignores other target-selection differences. With no common
targets, the report explicitly states that there is no comparable closure.

Text output shows at most ten entries per category and target, with exact total
counts. JSON is a complete standalone report (`comparison: "semantic"`,
`format_version: 1`) containing the selected scope, aggregate summary, per-target
summaries and `changes` with role lists in `old` and `new`, plus unmatched targets.
It does not enumerate all paths. Run `bst-key-explain` for the cache-key evidence report.
Colors follow the usual `--color` and `NO_COLOR` rules.

### Standalone closure TUI

`bst-closure-diff --tui` opens a dedicated comparison. Select a common target
and use **e** to switch between two views:

- **Closure effects** lists additions, removals, role changes and dependencies
  that are still reachable by a changed route. It follows the selected scope.
  The detail pane shows before/after witness paths and relevant changed edges.
  With a single-role scope, it also shows the full role transition.
- **Declared edges** lists exact changes to `consumer → dependency` declarations
  under the target in either snapshot. This view covers all edge types
  regardless of the closure scope. For example, it shows `build/run → build`
  even if another runtime path still reaches the dependency. Replacing
  `bootstrap.bst` with `linux-base.bst` appears as a removed and an added edge.

Use **t** for target, **s** for scope, **f** for closure category, **/** to search,
**Enter** to focus the detail pane, **Esc** to return to the list and **q** to exit.
`--all` includes unchanged closure members. `--check` still uses the comparison
scope supplied on the command line when the app exits.

The legacy diff inspector still opens its original reachability screen via **m**
or `bst-tree diff --semantic --tui`. Press **Esc** there to return to input changes.

Reachability describes declared elements, not the final staged file set or a
prediction that a build will run. The comparison is exact for the loaded snapshot
graphs and does not search for consumers outside them.

## Interactive change analysis

Capture resolved inputs **before and after** the change:

```sh
bst-snapshot -C /path/to/project app.bst --details -o before.json
# Change source refs, element configuration, or project settings.
bst-snapshot -C /path/to/project app.bst --details -o after.json
bst-graph-diff before.json after.json --tui
# For scripts / CI:
bst-key-explain before.json after.json --format json --check
```

`--details` adds effective `config`, `vars`, `env`, and `public` mappings to the
same framed `bst show --deps all` query that captures the graph. No build, track,
source checkout or artifact export is run. As with ordinary graph capture,
project loading can require junction sources and project plugins. The two
snapshots should use the same targets, options and BuildStream version.
Detailed snapshots may be substantially larger and can contain environment values,
URLs or credentials present in project configuration. Treat them as project data.

The inspector opens on **All**, with the requested targets from either snapshot
listed first, including unchanged targets. Press **1** to focus on observed local changes. The header always reports the total changed elements and category
counts. The left pane lists each element once. The overview on the right shows
a short summary and a color-coded diff for each changed section except the cache key.
Within replacement lines, changed words and punctuation have a contrasting background
in both the overview and the full section diff. Long diffs are abbreviated only in the overview. Related-element lists show up
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
the element list above the detail pane. For identical snapshots or context-only
changes, the targets remain selectable and the header reports zero changed elements.

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
alone does not trigger `--check`. Use `bst-key-explain` for a bounded text analysis
or `bst-key-explain --format json` for the complete evidence report.
Snapshot and noninteractive diff commands still do not import Textual.

### Input sources on both sides of a diff

Capture source locations with each snapshot to connect changed resolved settings
to their winning YAML definitions and transitive variable references:

```sh
bst-snapshot app.bst --provenance --history -o before.json
# Change project inputs, or use different --option values for the second snapshot.
bst-snapshot app.bst --provenance --history -o after.json
bst-key-explain before.json after.json
bst-key-explain before.json after.json --history
bst-key-explain before.json after.json --format json
bst-graph-diff before.json after.json --tui
```

`snapshot --provenance` implies `--details` and uses the private backend for
every query in the snapshot. Each query can capture up to 256 MiB per output stream
with provenance or 32 MiB otherwise. For larger graphs, pass e.g.
`--output-limit 536870912` (512 MiB) to `bst-snapshot`. Add `--history` to capture the full YAML override
chain as well as winning locations and references. Provenance is queried
for the whole selected graph in one invocation. A repeated framed graph/details
query detects project drift. Exact resolved details come from the provenance
JSON export: ordinary `show` YAML is a display format that can fold strings or
coerce scalar types. Every saved provenance value must match these lossless
resolved details. This is not a filesystem transaction; avoid editing the project
while capturing a snapshot. No build or artifact export is performed.

`bst-key-explain` shows each changed input's before/after value and independently
saved source locations, with transitive variable evidence. Applied conditions are
shown when captured; the full override chains remain hidden until `--history`
is requested. In the TUI, choose **Input sources** for all changed inputs and press
`h` to toggle history. Individual config/vars/env/public section diffs also show
their input sources. Text and the overview are abbreviated; JSON exports all
changed inputs and sources, and `--history` includes their full saved chains.

Sources are supporting evidence, not proof of cache-key or build causality.
Lists are compared as whole inputs, retaining their captured container or member
origins rather than pairing commands by shifted indices. Missing provenance or
history on one side is reported explicitly; added/removed values are distinguished
from JSON null. A change only in YAML location/history does not create a resolved
input difference or affect `--check`. Field filters still apply. Snapshot-only
diff works without `bst`, project files, or Textual.
Use the same capture mode for both sides. Mixing older YAML-based `--details`
snapshots with lossless provenance snapshots can introduce scalar-type or
whitespace differences from the display encoding; the explanation warns about
this and recommends recapturing both with `--provenance`.

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
with a separate, validated per-element `details` map. `snapshot --provenance`
writes version 3 with an additional `provenance` map covering exactly those nodes.
All three versions are readable. Provenance entries, reference coverage, and
resolved values are validated on load.
Snapshots contain canonical targets, metadata (bst version, project
options and strict mode), a map of nodes keyed by full junction-qualified name,
and typed directed edges. Basic/detailed snapshots have no timestamps or absolute
project paths; provenance snapshots retain filenames as supplied by BuildStream.
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
python -m pip install -e ./contrib/bst-tools-core -e './contrib/bst-tree[test,tui]'
python -m pytest -c contrib/bst-tree/pyproject.toml contrib/bst-tree/tests
# Opt in only with a working bst and buildbox-casd installation:
BST_TREE_TEST_LIVE=1 python -m pytest -c contrib/bst-tree/pyproject.toml contrib/bst-tree/tests
```

The implementation separates CLI parsing (`cli.py`), public BuildStream CLI
queries (`adapter.py`), graph validation and snapshots (`model.py`), comparison
(`diff.py`), change evidence (`explain.py`, with compact build-input reachability
in `keygraph.py`), target-relative semantic comparison (`semantic.py`), and
terminal navigation (`tui.py`, `diff_tui.py`, `semantic_tui.py`, and `closure_tui.py`). Element inspection lives in
`inspection.py` and `inspection_tui.py`. Snapshot and noninteractive diff commands
do not import Textual. Each interactive inspection owns a cancellable command runner and its
temporary exports; late background results cannot update a closed viewer.

Tests cover parsing, snapshots, comparison filters, scoped graphs, cycles,
headless navigation, inspection, cancellation, and temporary-file cleanup.
Live tests exercise a real project and artifact exports. They use an explicit
Linux/aarch64 sandbox target without executing target binaries. On macOS,
`buildbox-casd` may require a higher file-descriptor limit such as
`ulimit -n 4096`; projects may also need an explicit `sandbox.build-arch`.

Artifact file comparison, ABI reports, diffoscope and Bloaty are provided by the
separate [bst-diff](../bst-diff/README.md) project.

## Headless queries and inspection

The headless graph and inspection commands run without a TTY or Textual.
They default to plain text. `--format json` produces one JSON document on stdout.
BuildStream diagnostics and errors go to stderr. Exit statuses are 0 for a
successful query (including an empty result), 2 for errors and 130 for cancellation.

```sh
bst-tree tree app.bst sdk.bst -C /project --scope build
bst-tree tree app.bst -C /project --match llvm --format json
bst-rdeps lib.bst app.bst sdk.bst -C /project --format json
bst-rdeps lib.bst app.bst -C /project --direct
bst-paths lib.bst app.bst -C /project --scope run --format json

# Offline queries need neither bst nor the original project:
bst-tree tree --snapshot graph.json
bst-rdeps lib.bst --snapshot graph.json --format json
bst-paths lib.bst --snapshot graph.json
bst-snapshot app.bst -C /project | bst-tree tree --snapshot - --format json
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
results, not snapshots to pass back to `browse --snapshot`. Use `bst-snapshot` to
produce a complete portable graph.

```sh
bst-inputs app.bst -C /project --format json
bst-source-files app.bst -C /project --format json
bst-source-files app.bst -C /project --files --path .config
bst-source-files app.bst -C /project --checkout --files --path app/main.c
bst-artifact-files app.bst -C /project --path usr/share/app.conf --format json
bst-artifact-files app.bst -C /project --pull --format json
```

`bst-inputs` returns structured effective `config`,
`vars`, `env` and `public` data, without executing build commands. `bst-source-files`
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

`bst-artifact-files` lists the selected artifact's paths and long metadata, with no
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

The graph query and inspection commands accept `--timeout SECONDS` (default 120 per bst invocation)
and `--output-limit BYTES` (default 32 MiB per captured stream). Overflow is an
error rather than a silently partial graph or inspection. Cancellation and timeout
terminate the owned subprocess group. These limits do not cap the size of a saved
graph read from disk or stdin, or the number of source files listed locally.

### Path between two elements

```sh
bst-path app.bst lib.bst -C /project
bst-path tool.bst lib.bst --snapshot graph.json
bst-path tool.bst lib.bst --snapshot graph.json --scope run --format json
```

`path FROM TO` finds one shortest directed path, following consumer-to-dependency
edges. Both endpoints may be any exact element names in the graph, including
junction-qualified names; FROM need not be a snapshot root. Live mode loads both
endpoints and their dependencies. Snapshot mode also accepts `--snapshot -` for
stdin, and cannot be combined with `-C` or `--option`.

Unlike `paths`, which queries all saved roots, `path` applies `--scope` relative
to FROM: `all` follows all dependencies, `run` follows runtime dependencies, and
`build` follows direct build dependencies and their runtime closure. Equal-length
routes are selected deterministically. Cycles are safe; identical endpoints
produce a one-node path. JSON includes `source`, `element`, and a `paths` list
with ordered `nodes` and typed `edges`. Exit status is 0 when a path exists,
1 when no path exists in the selected scope, and 2 for errors such as an absent
element. An empty `paths` list represents no path.

### Snapshot timeout

`snapshot` defaults to **300 seconds (5 minutes) per bst invocation**, including
detail, provenance and history capture. Override it with `--timeout SECONDS`:

```sh
bst-snapshot app.bst -o graph.json --timeout 600
```

## Artifact and closure sizes

```sh
bst-du product.bst sdk.bst -C /project --scope run --format json
bst-du product.bst -C /project --check-max 100000000
bst-du --archive app.bst app.tar --archive runtime.bst runtime.tar
bst-du --snapshot graph.json --archive app.bst app.tar --archive runtime.bst runtime.tar
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

For binary growth attribution, use [bst-diff --bloaty](../bst-diff/README.md).

## Configuration provenance

```sh
# From this checkout, install just the private backend for an existing bst-tree:
./contrib/bst-tree/install-provenance.sh
bst-origin app.bst -C /project --path /env/CFLAGS
bst-origin app.bst -C /project --path /config/build-commands --format json
bst-origin app.bst -C /project --option debug True --path /vars/flags
bst-origin app.bst -C /project --history --path /env/CFLAGS
bst-origin app.bst -C /project --history --path /config/build-commands --format json
"${XDG_DATA_HOME:-$HOME/.local/share}/bst-tree/provenance/bst-provenance" show --deps none --provenance app.bst > origins.json
bst-origin --from-report origins.json --path /env/CFLAGS --format json
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
locations; the frontend does not guess origins by searching `.bst` files.

The installer creates a private virtual environment under
`${XDG_DATA_HOME:-$HOME/.local/share}/bst-tree/provenance`, installs
BuildStream 2.8.0, and compiles the checkout's native history extension.
It requires Python 3.10+, Python development headers and a C compiler.
The private environment includes `buildstream-plugins==2.7.0`,
`buildstream-plugins-community==2.3.0`, and `requests==2.33.1`.
Install additional project-specific plugins with that directory's `venv/bin/python -m pip install`.
Rerun the installer after updating the checkout. The system `bst` stays on its
original version. Ordinary snapshots still use `bst` on PATH. Live provenance
also supports a patched `bst` on PATH if the private backend is absent.
Remove the private directory to uninstall the backend, including its plugins and overlays.

Every report has `format_version: 1`, `query: "input-provenance"` and `elements`.
Entries contain the RFC 6901 `path`, unexpanded composed `raw`, resolved `value`,
`value_available`, `origin` (file, display, 1-based line/column, synthetic marker)
and variable `references`. The wrapper adds requested paths, selection/evidence
markers and any unavailable references. Generated/synthetic nodes have null
file/line/column; unavailable resolved values are explicitly marked. Reports
include effective environment/configuration values and should be handled like the
project's other configuration dumps.

The default view describes **winning composed values**. Add `--history` to
capture actual applied definition lineage in the native YAML composition engine:
project/plugin defaults, includes, element overrides and applied conditional
fragments keep their source file, 1-based line and column. Scalar steps are marked
`overridden` or `effective`. List containers also appear as entries: their history
records ordinary list replacements and explicit `(=)`, `(<)` and `(>)` directives;
steps still contributing to a final list are marked `contributes`. Applied
conditions carry their expression and the condition's own YAML location.

History is opt-in and starts before configuration/project loading. Normal loads
retain no history or node references. It requires **rebuilding this fork's native
`node` extension**, in addition to updating its Python modules; updating bst-tree
or copying only the Python files is insufficient. Ordinary winner-only queries
remain available without requesting history. Generated scalar assignments are
explicitly synthetic, even where BuildStream internally inherits old coordinates.

```sh
"${XDG_DATA_HOME:-$HOME/.local/share}/bst-tree/provenance/bst-provenance" show --deps none --provenance --history app.bst > history.json
bst-origin --from-report history.json --history --path /env/CFLAGS
```

History reports add `history_available: true` and per-entry `history` arrays with
raw definitions/directives, origins, operation, state and optional applied
conditions. `--history --from-report` fails explicitly if the requested history
was not captured. Without `--history`, the wrapper omits the extra history fields
and keeps its compact view. Old definitions are shown as raw inputs, not as
hypothetical resolved values; reference following uses the effective definition.

Definition precedence is not a build-execution trace or a reconstruction of every
assignment: inactive branches are not applied, and direct plugin/internal
mutations outside the observed composition/generated-assignment operations are
not reconstructed. Values removed entirely from the effective configuration have
no selected winning path in this report.

Raw values preserve pre-variable-expansion content after YAML
composition, not source quoting or original file formatting. Variable links are
expansion evidence, not proof that a compiler executed those flags, nor a trace
of transformations inside element plugins. Project includes may require junction
source retrieval during loading; no artifact checkout, build, source checkout or
integration commands are requested by the provenance query.

All output is headless text or JSON. Normal success exits 0; invalid inputs,
unsupported backends and unavailable referenced evidence exit 2, interruption 130.
The existing per-command timeout and output bounds apply. Native backend tests
compile the real node extension and cover composed includes, overrides, conditional
options, list directives, generated assignments, references, observer cleanup and unchanged
ordinary `bst show`; CI tests the fork's pure Python changes against the released
BuildStream compiled runtime on Python 3.10/3.14.


## Shared CAS index

Install the optional [bst-cas](../bst-cas/README.md) project in this tool's
environment and point it at the actual local BuildStream CAS:

```sh
python -m pip install ./contrib/bst-tools-core ./contrib/bst-cas ./contrib/bst-tree
export BST_CAS_DIRECTORY="${XDG_CACHE_HOME:-$HOME/.cache}/buildstream/cas"
bst-du product.bst --format json
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

### Plain dependency chains

```sh
bst-chains app.bst lib.bst -C /project
bst-chains app.bst lib.bst --snapshot graph.json
bst-chains app.bst lib.bst --snapshot graph.json --max-chains 10000
```

`chains FROM TO` prints simple directed dependency paths, one per line, without
headings or tree decoration:

```text
app.bst -> mid.bst -> lib.bst
app.bst -> tool.bst -> lib.bst
```

Unlike `path`, this includes longer alternative routes. Nodes never repeat within
a chain, so cycles do not produce infinite output. Routes have deterministic
lexicographic traversal order, not length order. The default limit is 1000 chains;
`--max-chains N` changes it. If more chains exist, the command prints the bounded
result, warns on stderr and exits 3. JSON (`--format json`) includes `paths`,
`max_chains` and `truncated`. Exit 0 means complete results, 1 means no chain
(empty text stdout), and 2 means an error. Scope, project options, exact element
names and snapshot/stdin input work as for `path`.

### Rebuild selection for one target

```sh
# Unique elements on ALL image -> vfs paths, dependencies first:
bst-rebuild-select image.bst vfs.bst -C /project
# vfs.bst somepkg.bst image.bst

# Show the corresponding chains:
bst-chains image.bst vfs.bst --snapshot graph.json
# image.bst -> somepkg.bst -> vfs.bst

bst-rebuild-select image.bst vfs.bst --snapshot graph.json --format json
```

`rebuild TARGET CHANGED` selects the union of dependency paths from TARGET to
CHANGED, including both endpoints. It excludes unrelated branches of the image,
consumers outside that target, and dependencies below CHANGED that are not on a
selected path. Shared intermediates appear once. The default `--format elements`
is a space-separated, shell-quoted list for pasting after `bst build`, ordered
with dependencies before consumers. It does not enumerate paths to build this
list, so graphs with many alternative routes do not cause exponential output.
Cycles within the selected subgraph are errors; unrelated cycles are ignored.

`bst-chains` prints routes in consumer-to-dependency direction with a separate
`--max-chains` limit and exit-3 truncation behavior. `bst-rebuild-select --format json` includes
`target`, `changed`, `elements`, typed `edges`, and `cache_checked: false`. No connection gives empty text stdout
and exit 1; an unknown endpoint gives exit 2. Live mode, saved snapshots, stdin,
project options and `--scope` are supported as for `path` (default scope: `all`).

This is a **structural candidate selection**, not a cache-aware build plan.
It includes runtime as well as build dependencies in the default scope and does
not inspect whether artifacts are already current. BuildStream `--no-strict`
may reuse an existing weak-key artifact even when that element is explicitly
listed in `bst build`. Pasting this list into that command therefore does not
force rebuilding the selected elements. The command does not delete artifacts,
modify dependency strictness or launch builds. Missing dependencies outside the
selection can still require building when BuildStream executes a build.

### Project option shorthand

Live query executables accept repeatable `-o NAME VALUE` / `--option NAME VALUE`.
`bst-snapshot` uses `-o FILE` for output and `--option NAME VALUE` for project options.
The previous `bst-tree -o NAME VALUE COMMAND` form also works.

```sh
bst-rebuild-select image.bst vfs.bst -o arch aarch64 -C /project
bst-snapshot image.bst --option arch aarch64 -o graph.json
```

Project options apply only when a command loads a live project.
