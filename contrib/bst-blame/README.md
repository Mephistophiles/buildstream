# bst-blame

Find which BuildStream artifacts contain a file and how their owning elements
are reached from your targets. Inspect overlapping paths without merging the
artifacts into a root filesystem or guessing which staged file wins.

This is an independent Python project with its own CLI, packaging, tests and CI.
It does not depend on `bst-tree`, `bst-grep`, Textual or ripgrep.

## Installation

Requires Python 3.10+ and a working **BuildStream 2.8+** installation, including
your project's plugins. The `bst` executable must be on `PATH`; it can live in a
different Python environment from bst-blame.

From the repository root:

```sh
pipx install ./contrib/bst-blame
bst-blame /usr/lib/libc++.so product.bst
```

Or install directly:

```sh
pipx install 'git+https://github.com/Mephistophiles/buildstream.git@bst-tree#subdirectory=contrib/bst-blame'
```

Use `pipx ensurepath` if the command is missing. To update a local installation,
run `pipx install --force ./contrib/bst-blame`.

## Usage

```sh
# Exact, case-sensitive path within each selected artifact.
bst-blame /usr/lib/libc++.so product.bst

# Search runtime dependencies only, with the same project options as your build.
bst-blame -C /path/to/project --option arch x86-64 --deps run /usr/lib/libc++.so product.bst

# Match a pattern across artifacts; quote it to prevent shell expansion.
bst-blame --glob '/usr/lib/libc++*' product.bst sdk.bst:tools.bst

# Find potentially overlapping paths, excluding shared directories alone.
bst-blame --duplicates --glob '/**' product.bst

# Compare bytes of overlapping regular files using SHA-256.
bst-blame --compare-content /usr/lib/libc++.so product.bst

# Include text diffs for small UTF-8 files.
bst-blame --diff /etc/example.conf product.bst

# Show reproducible follow-up commands for owners' config, sources and artifacts.
bst-blame --commands /usr/lib/libc++.so product.bst

# Structured output for scripts and editors.
bst-blame --json --duplicates --glob '/usr/lib/**' product.bst > owners.jsonl
```

`--deps all` is the default: explicit targets and all transitive dependencies.
`none` selects explicit targets; `run` selects targets and their runtime closure;
`build` selects build dependencies and their runtime closure, excluding the
targets themselves. Selection is delegated to `bst show` and queries run in
strict mode. Owners always retain their canonical junction-qualified names.
Shared elements are inspected once.

Leading `/` is optional; `/usr/lib/a` and `usr/lib/a` refer to the same
artifact-root path. These are not paths on the host machine. Paths containing
`..`, NUL or line breaks are rejected. `--glob` uses case-sensitive Python
`fnmatch` patterns: `*` also matches `/`, and `**` has no separate meaning.
This command does not implement Git line blame.

## Ownership, overlap and coverage

Each result shows its owners, entry types and sizes, executable flags, symlink
targets, and one shortest dependency path per applicable target. Paths respect
the selected dependency scope and do not enumerate every possible route.
`--commands` prints commands only; it does not execute any follow-up operation.

The default scan reads **locally cached artifact listings only** using public
`bst show` and `bst artifact list-contents` commands. It does not pull artifacts,
build, fetch element sources, run integration commands, or export file contents.
Project loading may still fetch junction sources as required by BuildStream.
Built-in `stack` and `junction` elements are skipped because they produce no
artifact file content. Failed builds with locally cached artifacts are included
and labelled `failed`.

Artifacts missing from the local cache are explicitly listed in the coverage
summary. A cache race or malformed listing is reported as an error. Valid owners
from other artifacts remain visible, but missing artifacts or errors make the
search incomplete and cause exit 2, even when matches were found. No match in an
incomplete scan does not prove that the path is absent from the whole build.

| Exit status | Meaning |
| --- | --- |
| 0 | Complete inspection with matching paths |
| 1 | Complete inspection without matching paths |
| 2 | Incomplete inspection or error |
| 130 | Interrupted |

Several artifacts containing the same path are a **potential overlap**, not
proof of a staging error. They may be selected in different sandbox scopes,
different locations, or with intentional overlap rules. This tool does not
calculate final staging order, integration effects or the winning owner.
Directories shared by multiple artifacts are labelled `shared-directory` and
are omitted by `--duplicates` unless that path also has a non-directory owner.

Equal sizes or metadata never imply equal file contents. Without comparison,
regular-file content is marked `not-compared`. Symlink targets are compared as
strings without following links. Mixed entry types are reported separately.

## Optional content comparison

`--compare-content` exports each matching owner's artifact once with
`bst artifact checkout --deps none --no-integrate --tar -`. Only overlapping
regular files are hashed. The tar stream is read without extraction or temporary
artifact archives; symlinks, hardlinks and special files are never followed.
The command checks that the artifact key still matches after export. File types,
sizes and executable flags must agree with the listing.

**Checkout may retrieve missing artifact data through configured remotes.**
Only comparison performs checkout; the default ownership scan does not. No build
or integration command is executed. There is no strict offline switch in this
version. Exporting a large LLVM artifact can take time and read substantial data
even when only one file is compared.

SHA-256 comparisons distinguish byte-identical and different contents; executable
flags and other displayed metadata are reported independently. `--diff` implies
comparison and adds unified text diffs against the first owner for UTF-8 files
without NUL bytes, up to 64 KiB per file. Each diff is limited to 200 lines.
Large and binary files retain hashes without a text diff. Results are not an
atomic snapshot if the project or cache changes during inspection.

## JSON Lines

Every object has `format_version: 1`. Each `type: "path"` record contains:

- `path`: normalized artifact-root path;
- `relationship`: `single-owner`, `shared-directory`, or `potential-overlap`;
- `metadata_equal`: whether all displayed metadata fields agree;
- `content_comparison`: `not-applicable`, `not-compared`, `different-types`,
  `identical`, `different`, or `incomplete`;
- `owners`: canonical `element`, `artifact_key`, cache `state`, `kind`, `size`,
  `executable`, `link_target`, `dependency_paths`, optional `sha256` and `commands`;
- `diffs`: source/destination owners, unified diff lines, and a truncation flag.

The final `type: "summary"` record contains `matched_paths` and `coverage` with
`selected`, `inspected`, `skipped`, `missing`, `errors`, and `complete`.
Startup errors and interruption emit `type: "error"` without a success summary.
Consumers should require a final `coverage.complete: true` and check exit status.
Text progress and coverage go to stderr; JSON never contains ANSI color codes.

## Implementation and development

The ownership index is in memory and restricted to the requested path/pattern.
Listings are batched with bounded argument counts. Elements having the same
unqualified name in different junctions are queried in separate batches so their
headers cannot mix owners. Paired plain/long listings preserve filenames with
spaces or ` -> `; inconsistent or unsupported output fails explicitly. Human
BuildStream listing output cannot reliably represent line-break filenames; they
are unsupported. There is no persistent index or dependency on private CAS APIs.

```sh
python -m pip install -e './contrib/bst-blame[test]'
python -m pytest -c contrib/bst-blame/pyproject.toml contrib/bst-blame/tests

# Real BuildStream integration requires working buildbox-casd, bubblewrap on
# Linux, and permission to create sockets. No target programs are executed.
python -m pip install -e './contrib/bst-blame[test,live]'
BST_BLAME_TEST_LIVE=1 python -m pytest -c contrib/bst-blame/pyproject.toml contrib/bst-blame/tests
```
