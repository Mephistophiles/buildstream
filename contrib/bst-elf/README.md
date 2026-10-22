# bst-elf

Inspect ELF dependencies and map providers to BuildStream artifact owners. This
independent Python 3.10+ project does not depend on bst-tree, bst-blame, a TUI,
readelf or a host dynamic loader. pyelftools reads ELF data without executing any
target program. BuildStream 2.8+ and project plugins are required for live inputs.

```sh
python -m pip install ./contrib/bst-tools-core ./contrib/bst-elf
bst-elf product.bst --path /usr/bin/app
bst-elf -C /project --option arch aarch64 product.bst --json --check
bst-elf product.bst --expect-machine EM_AARCH64 --path /usr/bin/app
# Offline saved checkout tar archives; no bst required:
bst-elf --archive application app.tar --archive runtime runtime.tar --json
```

`--deps run` is the default; `none`, `build` and `all` delegate selection to
BuildStream. Build scope excludes the targets. Each selected cached non-stack,
non-junction artifact is exported once with `bst artifact checkout --deps none
--no-integrate --tar -`. **Checkout may retrieve missing data from configured
remotes**; no build, source checkout or integration command runs. Missing artifacts
are reported as incomplete coverage. Failed cached artifacts are included. Project
loading can fetch junction sources. There is no strict offline switch for live
projects; use `--archive OWNER TAR` for offline analysis.

Temporary archives are removed on success, error or interruption. Archive paths,
duplicates and non-directory ancestors are checked. No archive member is extracted:
regular ELF files are parsed through bounded archive-member streams. Symlinks and
hardlinks are resolved only inside the virtual target filesystem, including absolute
symlink targets; host files are never followed. A link cycle is an explicit error.
Live exports check full cache keys and artifact CAS digests before/after export,
and check the graph key, to detect cache/project races. These are identity checks,
not a persistent CAS index, and the full scan is not an atomic project snapshot.

## Search profiles

The generic model searches own DT_RPATH (only without DT_RUNPATH), inherited RPATH,
explicit `--library-path DIR` entries, own DT_RUNPATH, then default directories.
RPATH is inherited by descendants; RUNPATH is not. DT_FLAGS_1 NODEFLIB suppresses
default directories. Defaults are `/lib` and `/usr/lib`, with **no host paths,
environment variables, ld.so.cache or automatic multiarch directories**. Needed
names containing `/` are interpreted as paths, not searched by SONAME. Providers
must be ET_DYN with matching ELF class, machine and endianness. SONAME is displayed;
it never substitutes a library file into an unsearched directory.

Use a JSON profile for KOS or a different target layout:

```json
{
  "format_version": 1,
  "default_dirs": ["/lib", "/usr/lib", "/lib64", "/usr/lib64"],
  "library_path": [],
  "lib": "lib64",
  "platform": "aarch64",
  "cwd": null,
  "inherit_rpath": true
}
```

```sh
bst-elf --profile kos.json product.bst --path /usr/bin/app --json
```

`$ORIGIN` / `${ORIGIN}` uses the resolved ELF member's directory; `$LIB` and
`$PLATFORM` require explicit profile values. Relative/empty search paths and
relative slash-containing NEEDED values require an explicit absolute target `cwd`.
Unknown tokens or unsupported paths are analysis errors, not silently ignored.
`--library-path` is repeatable and prepends explicit directories to the profile's
library-path list. For live targets, the resolved `LD_LIBRARY_PATH` from
`bst show --deps none --format '%{env}'` is appended after those explicit
paths. This includes project/element environment composition, variables and
selected `--option` values. All paths refer to the target filesystem.
Multiple targets must have the same resolved value; otherwise inspect them
separately. Dependency elements' build environments are not merged into it.
Offline `--archive` mode uses only the explicit profile/CLI paths.
The host's `LD_LIBRARY_PATH` and `shell.environment` overrides are not read.

This is a **declared-dependency model, not a complete loader emulator**. It does
not model symbol binding, version requirements, dlopen, LD_PRELOAD, secure execution,
loader caches, hwcaps, or modifications made by integration commands. A successful
result is not a guarantee that a program will load or run. Profiles describe
search assumptions and do not claim to implement every KOS/glibc loader behavior.
At the first existing candidate path, non-ELF/non-shared/incompatible files are
reported; the tool conservatively does not assume a loader would skip them.

Multiple owners at the same candidate path remain ambiguous, even with identical
bytes or only one architecture-compatible owner. No staging winner is guessed.
Directory and library symlink owners are retained in dependency link evidence.
Use `--path` repeatedly to select ELF roots and follow resolved NEEDED edges in
context. Without it, every regular ELF member is audited independently as a root;
that includes object files and shared libraries. Interpreter paths are checked,
but do not recursively introduce their own dependency contexts. Unknown or invalid
root paths are errors. Absent providers apply only to the selected artifact scope.

## Output and limits

Plain text is default; `--json` / `--format json` emit one versioned JSON document.
`files` contains owner/path identities, SHA-256, architecture, type, OSABI,
interpreter, SONAME, RPATH/RUNPATH and NEEDED lists. Live records add the artifact
key, digest and scoped shortest dependency paths. `contexts` retains each root,
file, inherited RPATH and resolution evidence. `findings` includes missing,
incompatible, ambiguous providers and unexpected architectures. `coverage.complete`
is false on missing artifacts, export/ELF parse failures or unsupported resolution.
Consumers should check both coverage and process status; startup errors emit
`complete: false` with `error` instead of a successful report.

Exit 0 means completed analysis; `--check` changes this to 1 for findings. No ELF
files also exits 1. Errors/incomplete scans exit 2, interruption 130. Text escapes
control characters; JSON contains no ANSI codes. `--timeout` defaults to 120 seconds
per bst command and kills the process group. Metadata output is bounded to 32 MiB
per stream. `--max-elf-size` defaults to 512 MiB per ELF; `--max-contexts` defaults
to 10000 and reports incomplete analysis if exceeded. Archive disk usage and
non-ELF file counts are not capped. By default, large artifacts require tar export. Optional direct CAS mode
reuses the shared persistent inventory and per-file ELF analysis cache.

Development: `pip install ./contrib/bst-tools-core './contrib/bst-elf[test]'`, then
`pytest -c contrib/bst-elf/pyproject.toml contrib/bst-elf/tests`.
The tests compile small real ELF files using `cc`, but never execute them.
Set `BST_ELF_TEST_LIVE=1` with BuildStream/buildbox installed for real cached-artifact
integration tests. CI runs these on Python 3.10 and 3.14.


## Shared CAS index

Install the optional [bst-cas](../bst-cas/README.md) project in this tool's
environment and point it at the actual local BuildStream CAS:

```sh
python -m pip install ./contrib/bst-tools-core ./contrib/bst-cas ./contrib/bst-elf
export BST_CAS_DIRECTORY="${XDG_CACHE_HOME:-$HOME/.cache}/buildstream/cas"
bst-elf product.bst --path /usr/bin/app --json
```

`--cas-directory DIR` also enables CAS mode; `--cas-index FILE` or
`BST_CAS_INDEX` chooses the shared SQLite database. Its default location is
`XDG_CACHE_HOME/bst-cas/index.sqlite3`. Trees are reused by artifact files digest;
current owners come from the live project. Missing or garbage-collected objects
remain explicit errors/incomplete coverage. CAS mode does not pull artifacts,
run integration or modify the BuildStream cache.

Static ELF metadata is cached per file digest and parser version; dependency
resolution still runs against the current selected graph and loader profile.
`coverage.cas_index` includes reuse statistics. Offline `--archive` inputs
continue to scan the specified tar archives.

Shared public CLI framing, process ownership and path validation live in
[bst-tools-core](../bst-tools-core/README.md); application packages do not import
each other.

CAS paths can also be discovered automatically from BuildStream user configuration
using `bst-cas elf ...`; see the [CAS wrapper guide](../bst-cas/README.md#search-without-configuring-cas-paths).
