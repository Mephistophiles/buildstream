# bst-diff

Compare BuildStream artifact files as text, JSON or an interactive terminal UI.
Supports exact content/metadata changes, abidiff ABI reports, diffoscope nested
formats/HTML and Bloaty binary-size attribution. Python 3.10+.

This command depends on [bst-tools-core](../bst-tools-core/README.md), and has no
dependency on bst-tree or any other application package. `bst-tree diff` remains
a graph snapshot comparison; this command compares artifact contents.

## Install

From the repository root, in your Python environment:

```sh
# Headless CLI, without Textual:
python -m pip install ./contrib/bst-tools-core ./contrib/bst-diff
# Add the optional interactive UI:
python -m pip install ./contrib/bst-tools-core './contrib/bst-diff[tui]'
```

Live inputs need `bst` and the project's plugins on PATH; they may be installed
in another environment. Offline tar comparison needs no BuildStream. Analyzer
executables are optional and installed separately. The package contains no
legacy command aliases or forwarding modules in bst-tree.

## Usage

Compare two immutable artifact refs (`project/normalized-element/full-key`), two
cached element names, or a ref and an element. An element name resolves against
the current project, so use refs to retain an older revision. Both checkouts use
the same project directory and repeated `--option NAME VALUE` settings.

```sh
bst-diff myproject/app/OLD_FULL_KEY app.bst -C /project --tui
bst-diff before.tar after.tar --archives --tui
bst-diff before.tar after.tar --archives --path etc/config --format json
bst-diff before.tar after.tar --archives --abidiff --diffoscope --check
bst-diff before.tar after.tar --archives --diffoscope-html report.html
bst-diff before.tar after.tar --archives --bloaty --path usr/lib/libapp.so
bst-diff before.tar after.tar --archives --bloaty --bloaty-sources sections,compileunits --format json
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

Install `abidiff` and `diffoscope` on PATH independently of bst-diff. For example,
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

`bst-diff --bloaty` profiles changed paired ELF files, or explicit selected
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

## Development

```sh
python -m pip install -e ./contrib/bst-tools-core -e './contrib/bst-diff[test,tui]'
python -m pytest -c contrib/bst-diff/pyproject.toml contrib/bst-diff/tests
# With real bst/buildbox and the external analyzers available:
BST_DIFF_TEST_LIVE=1 BST_DIFF_TEST_TOOLS=1 python -m pytest -c contrib/bst-diff/pyproject.toml contrib/bst-diff/tests
```

CI tests Python 3.10 and 3.14, actual BuildStream exports, abidiff, diffoscope,
Bloaty and the TUI independently of bst-tree. Archive safety and process
ownership use the common core implementation.
