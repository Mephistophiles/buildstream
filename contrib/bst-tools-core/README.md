# BuildStream tool suite and bst-tools-core

Six user-facing commands share a small implementation library:

| Package / command | Responsibility |
| --- | --- |
| `bst-tree` | Dependency graph browsing, snapshots, structural/semantic graph diff, inspection, provenance and closure sizes |
| `bst-diff` | Artifact file/metadata comparison, abidiff, diffoscope, Bloaty; headless and optional TUI |
| `bst-elf` | Static ELF dependency/provider audit |
| `bst-blame` | File ownership, overlaps and content comparisons |
| `bst-grep` | Source/workspace, resolved-configuration and CAS artifact searches |
| `bst-cas` | Shared digest-keyed inventory/analysis index and its maintenance CLI |
| `bst-tools-core` | Internal library; no command or application dependency |

The domain applications do not import one another: their shared dependencies
are bst-tools-core and, optionally, the bst-cas library. Shared functionality belongs
here; specialized artifact comparison belongs only to bst-diff, and graph
snapshots/diffing belong only to bst-tree. There are no old command aliases or
implementation forwarding modules. CAS remains a focused optional index rather
than a general utility dependency.

## Install from this checkout

Requires Python 3.10+. For pipx, run the installer from any working directory:

```sh
./contrib/install-bst-tools.sh             # All six commands, including TUIs
./contrib/install-bst-tools.sh --force     # Update after git pull
# Or omit TUI dependencies on a fresh installation:
./contrib/install-bst-tools.sh --headless
```

The script uses one pipx environment named `bst-cas`. It installs the local
`bst-tools-core` library before injecting its consumers with `--include-apps`.
It also passes the local core path to pip during each consumer installation,
so forced dependency resolution does not look for this unpublished package on
PyPI. A separate `pipx install bst-tools-core` would not work: it has no application,
and separate pipx environments do not share installed Python libraries.
BuildStream remains independent and is invoked through `bst` on PATH.
Use `PIPX_DEFAULT_PYTHON=python3.12` (or another Python 3.10+) to choose the
interpreter for a new environment, and `pipx ensurepath` if app links are not
on PATH. `--force` reinstalls checkout packages and replaces existing app links;
check `pipx list` first if these commands already belong to other environments.
The installation is not editable: rerun with `--force` after checkout changes.
`--headless` does not remove TUI dependencies from an existing environment.
Remove the complete suite with `pipx uninstall bst-cas`.

Equivalent manual commands, from the repository root:

```sh
pipx install ./contrib/bst-cas
pipx inject bst-cas ./contrib/bst-tools-core
pipx inject --include-apps bst-cas './contrib/bst-tree[tui]'
pipx inject --include-apps bst-cas './contrib/bst-diff[tui]'
pipx inject --include-apps bst-cas ./contrib/bst-elf
pipx inject --include-apps bst-cas ./contrib/bst-blame
pipx inject --include-apps bst-cas ./contrib/bst-grep
```

Alternatively, use a dedicated virtual environment:

```sh
python -m venv /path/to/bst-tools-env
. /path/to/bst-tools-env/bin/activate
python -m pip install ./contrib/bst-tools-core './contrib/bst-tree[tui]' './contrib/bst-diff[tui]' ./contrib/bst-elf ./contrib/bst-blame ./contrib/bst-grep ./contrib/bst-cas
```

Omit `[tui]` on bst-tree and bst-diff for a headless installation without
Textual or Rich. Install just the needed applications plus bst-tools-core if
you prefer separate environments. The library is installed from this checkout
alongside its consumers; no published PyPI release of this fork is assumed.
Re-run the same command to update all packages. BuildStream and its project
plugins may live in a separate environment; their `bst` must be on PATH.
External tools such as ripgrep, abidiff, diffoscope and Bloaty are installed
independently; see the respective application READMEs.

## Library modules

- `project`: strict public bst command construction, framed multiline `show`
  templates/parser, selected artifact graphs, dependency paths, source/workspace
  contexts and resolved configuration. It imports no BuildStream internals.
- `process`: bounded output, timeouts, binary stdout sinks, process-group
  cancellation, a text CLI adapter, and streaming child ownership.
- `archive`: safe manifest parsing, duplicate/ancestor/hardlink validation,
  SHA-256 inventory, bounded previews and verified private materialization.
- `paths`: artifact-root path normalization; no host path lookup.
- `cli`: numeric argument types and optional TUI dependency checks.
- `ui`: optional Rich diff styling, with no Textual screens or application imports.

Snapshots and their graph-specific interpretation remain in bst-tree. ELF
parsing/resolution remains in bst-elf. Ownership policy remains in bst-blame.
Search/ripgrep event handling remains in bst-grep. Analyzer execution and its
exit-status meanings remain in bst-diff. These are distinct domain behaviors,
not interchangeable copies of the shared adapters.

## Development

```sh
python -m pip install -e './contrib/bst-tools-core[test]'
python -m pytest -c contrib/bst-tools-core/pyproject.toml contrib/bst-tools-core/tests
```

Tests cover bounded output, binary streaming, timeouts, cancellation and
architectural import boundaries. Application suites exercise their contracts
against the same common implementations. CI also installs only the base
bst-tree and bst-diff distributions in a fresh environment, verifies Textual is
absent, and runs actual offline graph/file comparisons, graph queries and sizes.
