# bst-cas

Shared, persistent inventories and per-file analyses for local BuildStream CAS.
This is an independent Python project used optionally by `bst-blame`, `bst-elf`,
`bst-grep --in artifacts`, and `bst-tree size`. The consumers still work without
this package in their existing modes.

## Install and use

From the repository root, install the packages in the **same environment**:

```sh
python -m pip install ./contrib/bst-cas ./contrib/bst-blame ./contrib/bst-elf ./contrib/bst-grep ./contrib/bst-tree

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
opt-in through the directory flag or `BST_CAS_DIRECTORY`. The SQLite file path
alone does not enable it. With pipx, inject this package into each consumer's
environment, for example `pipx inject bst-elf ./contrib/bst-cas`.

The directory is explicit because a user's `cachedir` may differ from the
default, and `bst` may run in a separate environment. No configuration path is
guessed and no private BuildStream Python API is imported. Public
`bst show --format '%{artifact-cas-digest}'` supplies the files-tree digest; an
unmodified BuildStream 2.8 works. This adapter reads BuildStream's local
`cas/objects/HH/HASH` layout and a wire-compatible projection of REAPI v2
Directory metadata. Future layout/hash changes require an adapter update.

## Cache semantics

- Tree inventories are keyed by SHA-256 **hash and size**, rather than element
  names, paths, options or cache keys. Different owners of identical trees share
  one record. Ownership and dependency paths are supplied by the current graph.
- Each live artifact's full key and digest are checked before and after loading
  its inventory. Changes are reported as incomplete, rather than attributed to
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
python -m pip install './contrib/bst-cas[test]' ./contrib/bst-elf ./contrib/bst-blame ./contrib/bst-grep './contrib/bst-tree[test]'
python -m pytest -c contrib/bst-cas/pyproject.toml contrib/bst-cas/tests
# Also needs bst/buildbox, gcc and ripgrep:
BST_CAS_TEST_LIVE=1 python -m pytest -c contrib/bst-cas/pyproject.toml contrib/bst-cas/tests
```

Tests cover cross-owner reuse, digest changes, parser namespaces, checksum
failures, CAS garbage collection, safe path handling, concurrency, atomic
failure behavior, all four consumers and real BuildStream CAS artifacts.
