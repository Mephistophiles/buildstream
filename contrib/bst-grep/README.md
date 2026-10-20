# bst-grep

Search BuildStream source files and resolved build configuration, with the owning
element attached to every result. This is an independent project: it does not
require `bst-tree`, Textual, or a terminal.

## Install

Requires Python 3.10+, **ripgrep (`rg`) 13+**, and a working **BuildStream 2.8+**
installation with the project's source and element plugins. Install ripgrep with
your system package manager (`apt install ripgrep`, `brew install ripgrep`, etc.).
The tool calls the `bst` and `rg` executables on `PATH`; BuildStream and its plugins
can live in a different environment from bst-grep.

From the repository root:

```sh
python -m pip install ./contrib/bst-tools-core ./contrib/bst-grep
bst-grep -C /path/to/project pthread_setspecific libcxx.bst
```

The shared `bst-tools-core` dependency is installed from the same checkout.
Re-run the install command after an update. See the [suite installation guide](../bst-tools-core/README.md)
for a single environment containing all commands.

## Usage

```sh
# Current sources of one element; use its live workspace when open.
bst-grep pthread_setspecific libcxx.bst

# Search each unique element in a target's full dependency graph.
bst-grep --deps all -g '*.{c,cc,cpp,h,hpp}' '__asan_' product.bst

# Literal, case-insensitive, whole-word matching with surrounding lines.
bst-grep -F -i -w --context 2 pthread_setspecific libcxx.bst

# Effective build configuration, variables, and environment for these options.
# -- ends option parsing for a pattern starting with '-'.
bst-grep --in config --deps all --option arch x86-64 -- '-fsanitize' product.bst

# Include a shortest dependency path from each applicable target.
bst-grep --deps all --why calloc product.bst sdk.bst:tools.bst

# Stream JSON Lines for scripts, editors, or CI.
bst-grep --json --deps all 'unsafe_function\(' product.bst > results.jsonl
```

`-C` always means **project directory**. Use `--context NUM`, `-A NUM`, or `-B NUM`
for context lines. Explicit `-A` and `-B` override their respective side of
`--context`, regardless of argument order. Patterns use ripgrep's default regex
syntax; `-F` selects literal text. Multiline and PCRE2 matching are not exposed.

| Option | Meaning |
| --- | --- |
| `--in sources` | Source files, the default |
| `--in config` | Effective `config.yaml`, `vars.yaml`, and `env.yaml` from `bst show` |
| `--deps none` | Explicit targets only, the default |
| `--deps run` | Targets and their runtime dependency closure |
| `--deps build` | Build dependencies and their runtime closure; excludes the targets themselves |
| `--deps all` | Targets and all transitive dependencies |
| `--option NAME VALUE` | Project option; repeat as needed |
| `-g GLOB` | Include/exclude relative paths; repeat, e.g. `-g '*.c' -g '!vendor/**'` |
| `--why` | One shortest dependency path per applicable target |
| `--json` | Versioned JSON Lines without color escapes |
| `--color auto/always/never` | Text coloring; auto respects `NO_COLOR` |

Dependency selection is delegated to `bst show`; it is not approximated by a
recursive YAML scan. Multiple and junction-qualified targets work, and shared
elements are searched once. Queries run in strict mode using the same project
directory and options throughout. `--why` loads the full selected targets' graph
and computes shortest paths once per root; it does not enumerate every route.

## Results

Text output contains a junction-qualified element name, a source-relative path,
a 1-based line number, a 1-based **byte column**, and the matching line:

```text
sdk.bst:llvm.bst::compiler-rt/lib/asan/asan_rtl.cpp:412:7:matching text
```

`::` separates the element from its file path. Context rows use `-` between the
path, line number, and text; disjoint context groups are separated by `--`.
With `--why`, `# target.bst -> ... -> owner.bst` precedes an element's first match.
Control characters in text output are escaped so filenames and file contents
cannot inject extra rows or terminal escape sequences. Summaries and progress go
to stderr; progress is shown only when stderr is a terminal.

JSON mode emits `match`, `context`, `error`, and finally `summary` records, each
with `format_version: 1`. A match looks like:

```json
{"format_version":1,"type":"match","element":"sdk.bst:llvm.bst","area":"sources","origin":"checkout","path":"lib/foo.cpp","line":42,"column":5,"text":"    needle();","submatches":[{"start":4,"end":10}]}
```

`submatches` uses zero-based byte offsets into the original line. `text` omits
the trailing newline; context records have `column: null`. Origins distinguish
`workspace`, `checkout`, and `resolved`. `--why` adds `dependency_paths` to match
and context records. Invalid UTF-8 line data additionally carries `text_base64`
containing the original bytes, including the newline. Invalid UTF-8 filenames
additionally carry `path_base64` containing the source-relative filename bytes.
The JSON `path` uses escaped filesystem surrogate characters for such names.

The summary contains `selected`, `searched`, `failed`, `matched_lines`,
`matched_files`, and `complete`. Counts refer to matching lines and element/path
pairs, rather than the number of individual regex occurrences. Errors for one
element do not prevent searching the others. Matches already emitted before an
error remain useful, but the search then exits 2 with `complete: false`.
Startup errors and interruption emit an error without a final summary; consumers
must require a final `complete: true` summary and check the process exit status.

| Exit status | Meaning |
| --- | --- |
| 0 | Complete search with matches |
| 1 | Complete search without matches |
| 2 | Invalid arguments, unavailable data, command failure, or incomplete search |
| 130 | Interrupted; child processes are stopped before temporary files are removed |

## Source and configuration semantics

Open workspaces are searched directly, including uncommitted edits, without
modifying or removing their files. Otherwise each element gets a temporary
`bst source checkout --deps none`. Results omit BuildStream's element/junction
wrapper directories, so a file has the same logical path in both cases.
Temporary exports are removed after each element, on error, or on interruption.

Checkout uses BuildStream's staging semantics, including all configured sources
and patch sources. **Checkout may fetch missing sources** from configured source
remotes or upstream; fetched data stays in BuildStream's cache. This version has
no cache-only/offline switch. Project loading can also fetch junction sources.
No build, track, prepare, integration, or generated build script is executed.
Generated checkout helper scripts are omitted; use `--in config` for effective
build commands. The source view is not a snapshot of a previous build.

Hidden files and files covered by `.gitignore`/`.ignore` are included. Repository
administrative directories `.git`, `.hg`, and `.svn` are excluded. Symlinks are
not followed, even inside workspaces. Ripgrep's binary detection applies; it
stops searching a file when it detects a NUL byte. Files are searched as raw
bytes without UTF-16 transcoding. The user's ripgrep configuration is ignored
to preserve consistent behavior and structured output.

Configuration search uses resolved values for the selected project options,
including inherited values and substituted variables. Its line numbers refer
to the displayed synthetic YAML files, **not the original `.bst` definitions**.
It does not search inactive conditional branches or identify the original YAML
location that supplied a value.

This first version searches sources and configuration. Artifacts, saved
buildtrees, persistent indexing, and direct CAS scanning are not implemented.
Each uncached source tree must be exported before searching; large projects such
as LLVM can take significant time and temporary disk space. Results stream while
each tree is searched; only one element checkout is retained at a time.

## Development

```sh
python -m pip install -e ./contrib/bst-tools-core -e './contrib/bst-grep[test]'
python -m pytest -c contrib/bst-grep/pyproject.toml contrib/bst-grep/tests

# Install the real BuildStream and patch source plugin used by the live fixtures.
python -m pip install -e ./contrib/bst-tools-core -e './contrib/bst-grep[test,live]'
# Requires working bst/buildbox-casd, ripgrep, patch, bubblewrap on Linux,
# and permission to create sockets.
BST_GREP_TEST_LIVE=1 python -m pytest -c contrib/bst-grep/pyproject.toml contrib/bst-grep/tests
```

The project has its own packaging and CI. `search.py` translates streaming
ripgrep events, and `cli.py` handles provenance and presentation. Public
BuildStream queries, scoped paths, source/configuration contexts and process
ownership are implemented once in `bst-tools-core`.
Tests exercise real ripgrep, malformed CLI data, globs, context, unusual filenames,
partial failure and cancellation. Opt-in live tests cover local sources, patched
sources, junctions, dependency selection, project options and dirty workspaces.


## Shared CAS index

Install the optional [bst-cas](../bst-cas/README.md) project in this tool's
environment and point it at the actual local BuildStream CAS:

```sh
python -m pip install ./contrib/bst-tools-core ./contrib/bst-cas ./contrib/bst-grep
export BST_CAS_DIRECTORY="${XDG_CACHE_HOME:-$HOME/.cache}/buildstream/cas"
bst-grep --in artifacts --deps run -g '*.h' pthread product.bst
```

`--cas-directory DIR` also enables CAS mode; `--cas-index FILE` or
`BST_CAS_INDEX` chooses the shared SQLite database. Its default location is
`XDG_CACHE_HOME/bst-cas/index.sqlite3`. Trees are reused by artifact files digest;
current owners come from the live project. Missing or garbage-collected objects
remain explicit errors/incomplete coverage. CAS mode does not pull artifacts,
run integration or modify the BuildStream cache.

The new `--in artifacts` mode requires bst-cas and a local CAS directory. It
searches verified regular-file copies in a private temporary tree, preserving
paths, hidden files, ripgrep globs and element ownership; target symlinks are not
followed. Results identify their tree with `origin: cas:HASH/SIZE`. This is an
artifact search, not a persistent full-text index. Existing source/workspace and
resolved-config searches remain live and are not cached by artifact digest.
