# bst-cas

## Search without configuring CAS paths

```sh
# File owners and dependency paths, using CAS automatically.
bst-cas blame /usr/lib/libc++.so product.bst
bst-cas blame --duplicates --glob '/usr/lib/**' product.bst

# Search artifact contents (defaults to --in artifacts).
bst-cas grep 'needle' product.bst

# Inspect ELF dependencies using CAS.
bst-cas elf product.bst --path /usr/bin/app --json
```

These wrappers run the corresponding installed `bst-blame`, `bst-grep`, or
`bst-elf` command from PATH and preserve its arguments, output and exit status.
They derive the CAS location automatically, with this precedence:

1. `bst-cas --cas-directory DIR ...`.
2. `BST_CAS_DIRECTORY`.
3. `cachedir` in `$XDG_CONFIG_HOME/buildstream2.conf`, falling back to
   `buildstream.conf` only if the versioned file does not exist; CAS is `cachedir/cas`.
4. `$XDG_CACHE_HOME/buildstream/cas` (XDG defaults apply when unset or empty).

Configuration paths support `~` and environment variables. Invalid configuration
is an error, not a silent fallback to another cache. If you run BuildStream with
an explicit non-default `--config` file, supply `--cas-directory` for that cache.
The wrappers do not build, pull artifacts or modify the BuildStream cache.
`--cas-index` is optional: the derived SQLite index uses its existing default.
Global wrapper options go before the subcommand; tool options follow it.

Automatic discovery also applies to `bst-cas index/find/stats/prune` and
`bst-grep --in artifacts`. `bst-cas find NAME` searches **previously indexed metadata only**.
Pass targets to search project artifacts without pre-indexing:

```sh
bst-cas find libc++.so product.bst
bst-cas find 'libc++*' product.bst --deps run
bst-cas find libc++.so product.bst -C /path/to/project --option arch x86-64
bst-cas find libc++.so                  # Previously indexed trees only
bst-cas find libc++.so --json           # Explicit machine-readable output
bst-cas stats                          # Actual CAS/index paths and index counts
```

A name without `/` matches that basename in any directory (case-sensitive glob).
A path with `/` is matched relative to the artifact root; a leading `/` is optional.
Live searches use `bst-blame` and show owners and dependency paths. `--digest`
is available only for indexed metadata searches. A complete search with no
matches exits 1; errors/incomplete live searches exit 2.

Built-in commands now print readable text by default; use `--json` before the
subcommand or after a built-in subcommand for the versioned JSON format.
`stats` counts **derived SQLite index** trees/analyses, not all BuildStream CAS
objects. Zero counts do not mean the CAS is empty. Check the displayed paths;
use a live target search to populate the index. Cached metadata search does not
verify whether its recorded objects still exist in CAS.


Shared, persistent inventories and per-file analyses for local BuildStream CAS.
This is an independent Python project used optionally by `bst-blame`, `bst-elf`,
`bst-grep --in artifacts`, and `bst-tree size`. The consumers still work without
this package in their existing modes.

## Install and use

From the repository root, install the packages in the **same environment**:

```sh
python -m pip install ./contrib/bst-tools-core ./contrib/bst-cas ./contrib/bst-blame ./contrib/bst-elf ./contrib/bst-grep ./contrib/bst-tree

# Use the actual local BuildStream cachedir + /cas. This example is the default.
export BST_CAS_DIRECTORY="${XDG_CACHE_HOME:-$HOME/.cache}/buildstream/cas"
# Optional: default is XDG_CACHE_HOME/bst-cas/index.sqlite3.
export BST_CAS_INDEX=/path/to/derived-index.sqlite3

bst-blame /usr/lib/libc++.so product.bst --compare-content --json
bst-elf product.bst --path /usr/bin/app --json --check
bst-tree size product.bst --format json
bst-grep --in artifacts --deps run -g '*.h' pthread product.bst
bst-cas stats
```

Each consumer also accepts `--cas-directory DIR --cas-index FILE`. CAS mode is
opt-in through the wrappers, directory flag or `BST_CAS_DIRECTORY` (artifact
search in `bst-grep` always uses CAS). The SQLite file path
alone does not enable it. With pipx, inject this package into each consumer's
environment, for example `pipx inject bst-elf ./contrib/bst-cas`.

Discovery reads BuildStream 2 user configuration without importing private
BuildStream Python APIs, even when `bst` runs in a separate environment. Public
`bst show --format '%{artifact-cas-digest}'` supplies the files-tree digest; an
unmodified BuildStream 2.8 works. This adapter reads BuildStream's local
`cas/objects/HH/HASH` layout and a wire-compatible projection of REAPI v2
Directory metadata. Future layout/hash changes require an adapter update.

## Cache semantics

- Tree inventories are keyed by SHA-256 **hash and size**, rather than element
  names, paths, options or cache keys. Different owners of identical trees share
  one record. Ownership and dependency paths are supplied by the current graph.
- Live full keys and digests are checked before and after each bounded batch
  of up to 64 artifacts (24 KiB target argument budget). Identical tree digests
  are loaded/validated once per batch, preserving separate owner records. Changes are reported as incomplete, rather than attributed to
  an old owner. A graph-wide atomic snapshot is not claimed.
- ELF metadata is keyed by the file digest and a versioned parser namespace,
  including the pyelftools version. Changed trees can reuse unchanged files.
  Loader resolution is rerun for each selected graph and search profile.
- Cold reads verify Directory checksums. Warm reads verify directory presence
  and checksums when their filesystem signature changes, and check file presence
  and sizes. Analysis/content reads also verify file checksums, with verification
  reused only for the same device, inode, size, mtime and ctime in the same CAS.
- Missing, malformed, mutated or garbage-collected objects produce incomplete
  coverage/errors. A cached inventory never stands in for a missing local tree.
  Objects are not pinned: concurrent CAS garbage collection may require a retry.
- SQLite WAL supports concurrent readers/writers. Only complete inventories and
  successful analyses are inserted, using atomic statements; parser errors and
  interrupted reads do not create successful analysis records. Derived JSON
  records have checksums and the database schema is versioned.

No tar checkout, extraction, artifact integration, target execution, remote pull,
CAS writes or artifact deletion is performed in this mode. Missing artifacts
must first be pulled with BuildStream. Remote storage-service configurations
need a populated local CAS; the index does not query remote services.

The index contains metadata and ELF analyses, **not** a second copy of file
contents, a CAS garbage collector, a physical-disk accounting tool, or a staging
winner. It does not preserve source YAML provenance. Node properties, Unix mode
and timestamps beyond the executable flag are not interpreted by this adapter.
Hardlinks in exported tar archives are represented by their independent logical
file paths in CAS. ELF records have `mode: null` and a separate `executable`
flag in CAS mode, rather than inventing Unix permission bits. Names containing
NUL or line breaks are rejected explicitly.

## Consumer behavior

`bst-blame` uses the inventory for file ownership, overlaps, sizes, executable
flags and symlink targets. `--compare-content` verifies the corresponding CAS
bodies; `--diff` reads bounded text from them. Neither needs artifact tar export.

`bst-elf` caches static ELF parsing, including NEEDED, SONAME, RPATH and RUNPATH.
File/size limits still apply to cached results. Non-ELF results are cached too;
oversized files receive only an uncached ELF-magic check. Resolution semantics,
ambiguities and existing `--check`/coverage exit statuses are unchanged.

`bst-tree size` uses inventory sizes and SHA-256 file digests. Logical per-path,
closure, root-exclusive and content-unique metrics retain their meanings. File
bodies are not rehashed for size accounting. Offline archive modes continue to
read the specified archives.

`bst-grep --in artifacts` searches current selected artifact files using the
existing ripgrep options and JSON Lines output. It copies verified regular files
to a private temporary tree, preserves relative paths and hidden files, and omits
target symlinks. Matches retain element ownership and `origin: cas:HASH/SIZE`.
Stack/junction elements are skipped. Searches still read file contents each time;
there is no persistent full-text index. `--in sources` searches current sources
and open workspaces directly, and `--in config` reads current resolved values;
neither is cached under an artifact digest.

CAS statistics are included in the consumers' JSON coverage/summary records.
Counters describe the current invocation; tree/analysis counts describe the
shared database. Console output stays compact.

## Headless index maintenance

```sh
digest=$(bst show --deps none --format '%{artifact-cas-digest}' app.bst)
bst-cas index "$digest"
bst-cas find '/usr/lib/*.so*'
bst-cas find '/usr/include/*' --digest "$digest"
bst-cas stats
bst-cas prune --older-than 30
```

These commands emit one versioned JSON document. `index` requires the local
objects to be present. `find` intentionally searches **indexed metadata only**:
its results are historical digest/path records, and do not assert current owners
or availability. Use `bst-blame` for live ownership. `prune` removes derived
records last accessed more than the specified number of days ago; zero removes
all existing derived records. It clears verification hints as well, and never
removes CAS blobs. Deleting the SQLite database, with all consumers stopped,
also safely resets the index. Unsupported/corrupt databases fail explicitly;
choose a fresh `--cas-index` path to rebuild.

Exit status: 0 success, 2 error/incomplete, 130 interrupted. Consumers retain
their own no-match, findings and budget exit statuses.

## Tests

```sh
python -m pip install ./contrib/bst-tools-core './contrib/bst-cas[test]' ./contrib/bst-elf ./contrib/bst-blame ./contrib/bst-grep './contrib/bst-tree[test,tui]'
python -m pytest -c contrib/bst-cas/pyproject.toml contrib/bst-cas/tests
# Also needs bst/buildbox, gcc and ripgrep:
BST_CAS_TEST_LIVE=1 python -m pytest -c contrib/bst-cas/pyproject.toml contrib/bst-cas/tests
```

Tests cover cross-owner reuse, digest changes, parser namespaces, checksum
failures, CAS garbage collection, safe path handling, concurrency, atomic
failure behavior, all four consumers and real BuildStream CAS artifacts.

## Batch query performance

All four consumers use the same bounded identity batches. A successful selection
of N artifacts normally needs `2 * ceil(N / 64)` identity queries, rather than
`2 * N`; long qualified names can lower the batch size. Failed queries are
bisected to retain unaffected owners. Nothing from a batch is published before
the second identity query. Shared trees are revalidated in later batches, so
concurrent CAS garbage collection still produces incomplete coverage.

`identity_queries` and `shared_tree_reuses` are reported in JSON statistics.
`hits`/`misses` count actual persistent tree loads, not repeated owners. A test
with 257 qualified owners uses 10 identity queries and five tree validations,
with distinct ownership preserved. This is an operation-count result, not a
wall-time promise for a particular project.

With the tool suite installed, measure the actual cold/warm derived index on
your project, including BuildStream startup and project loading:

```sh
python -m bst_cas.benchmark -C /project --cas-directory /actual/cachedir/cas --deps run product.bst
```

The benchmark emits JSON for single-owner and batched modes and checks their
identities/inventory counts agree. It creates only temporary derived databases;
it does not flush OS/BuildStream caches, build targets, pull artifacts or modify
CAS. A live integration test also exercises this benchmark on real artifacts.
