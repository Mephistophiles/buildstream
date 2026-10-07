# bst-mc: BuildStream in Midnight Commander

Browse a BuildStream project as a read-only virtual filesystem in Midnight
Commander (MC). Follow dependencies, inspect resolved build commands, and open
source or artifact files using MC's regular panels and viewer.

For a standalone terminal graph viewer and portable snapshot comparison, see
[`bst-tree`](../bst-tree/README.md). `bst-mc` does not require bst-tree or Textual.

## Requirements and installation

You need Python 3.10+, MC with extfs support, and a working `bst` command on
`PATH`, including the plugins required by your project. The plugin invokes the
public BuildStream CLI; it does not import BuildStream's Python modules.

From the root of this repository:

```sh
pipx install ./contrib/mcplugin
bst-mc --install-mc
bst-mc /path/to/project app.bst
```

Installation has two steps: pipx installs `bst-mc` (the launcher) and `bstmc`
(the extfs helper); `--install-mc` registers the helper in your MC user's
`extfs.d` directory. It locates that directory through MC and writes a launcher
bound to the installed package's Python interpreter. No sudo is needed.
An existing helper owned by this package is updated; an unrelated file is not
replaced. Restart any running MC sessions after registration.

If `bst-mc` is not found, run `pipx ensurepath` and open a new terminal. Keep an
existing working BuildStream installation. If installing BuildStream with pipx,
install your project's plugins into that same environment:

```sh
pipx install buildstream
pipx inject buildstream PLUGIN_PACKAGE
bst --version
mc --version
```

Replace `PLUGIN_PACKAGE` with the required package name. If `bst` lives in a
virtualenv, activate it before launching MC. BuildStream's own runtime tools,
such as `buildbox-casd`, must also be available as required by your installation.

To update from a changed local checkout:

```sh
pipx install --force ./contrib/mcplugin
bst-mc --install-mc
bst-mc --version
```

Restart MC to discard old helpers, virtual paths, and cached listings.

## Open a project

These forms are equivalent:

```sh
bst-mc /path/to/project app.bst
bst-mc -C /path/to/project app.bst
```

Without `-C`, the first positional argument is treated as the project directory
only if it is an existing directory. Inside a project, use `bst-mc app.bst`.
Multiple targets, junction-qualified names, and repeated project options work:

```sh
bst-mc -C /path/to/project --option arch x86-64 app.bst sdk.bst:tools.bst
bst-mc /path/to/project
```

With no targets, BuildStream resolves the project's default targets. Queries use
strict mode and the selected project options. Before launching MC, `bst-mc`
checks helper registration and loads the graph; setup or project errors appear
in the terminal instead of opening an unusable panel.

Start in **`all/targets/`**, open a target, then follow `dependencies/`. Read
`README.txt` inside the virtual filesystem for an in-panel guide.

## Bookmarks and an existing MC session

Create a persistent bookmark without starting MC:

```sh
bst-mc /path/to/project app.bst -o ~/project.bstmc
```

In MC's command line, enter `cd ~/project.bstmc/bstmc://`.
A bookmark stores the absolute project directory, targets, and options as JSON.
It is a live project reference, not a portable graph snapshot. Creation does not
load the graph or require MC, and refuses to overwrite an existing file.
Opening it requires the registered helper and a working project.

To open `.bstmc` files with Enter, use **Command → Edit extension file** in MC
and merge this section into your user `mc.ext.ini` **before `[Default]`**,
preserving other associations (also provided in [`mc.ext.ini`](mc.ext.ini)):

```ini
[buildstream-project]
Shell=.bstmc
Open=%cd %p/bstmc://
```

For older MC installations using `mc.ext` instead of `mc.ext.ini`:

```text
shell/.bstmc
    Open=%cd %p/bstmc://
```

This association is optional for the launcher and direct `cd` commands.
Nested `.tar` archives use MC's standard archive association.

## Navigation and virtual files

| Location | Contents |
| --- | --- |
| `all/` | Full dependency graph |
| `build/` | Direct build dependencies of the targets and their runtime closure, with targets retained as roots |
| `run/` | Targets and their runtime dependency closure |
| `<scope>/targets/` | Links to the selected targets |
| `<scope>/elements/` | Canonical element directories, grouped by project paths |
| `<scope>/tree.txt` | Bounded dependency tree with build/run edge labels; view with F3 |
| `<element>/dependencies/` | Links to dependencies with `[build]`, `[run]`, or `[build+run]` labels |
| `<element>/reverse-dependencies/` | Links to consumers within the selected scope |

Enter opens directories and links; `..` goes up. Alt-y returns to an earlier
directory in MC's history, and Ctrl-s searches panel names. Shared dependencies
link to the same canonical element directory. `tree.txt` expands shared subtrees
once and marks later occurrences `[see above]`, also bounding cycles.

Project subdirectories remain visible: `default/lib.bst` appears under
`elements/default/lib.bst/`. Junctions become marked directories, for example
`sdk.bst:base/lib.bst` becomes `elements/sdk.bst [junction]/base/lib.bst/`.
Nested junctions form nested directories. Literal `::` is displayed as
`[junction x2]` and passed back unchanged; this does not redefine BuildStream's
junction syntax. Spaces and brackets in actual names are encoded to avoid
collisions. `element.json` always contains the original element name.

Each element directory contains:

| File | How to use it |
| --- | --- |
| `element.json` | F3: name, kind, cache key, source provenance, and workspace presence |
| `paths.txt` | F3: one shortest path from each applicable target in the selected scope |
| `source-info.txt` | F3: source provenance from `bst show --format '%{source-info}'` |
| `build-commands.txt` | F3: resolved configuration, variables, and environment from `bst show` |
| `sources.tar` | Enter: export with `bst source checkout --deps none --include-build-scripts --tar …` |
| `artifact.tar` | Enter: pull the artifact if missing, then export with `bst artifact checkout --deps none --no-integrate --tar …` |

Build commands are effective settings, not the original YAML or a build log;
available commands depend on the element kind. Inside either archive, Enter
browses directories, F3 views files (including binary files), and F5 copies files
to the other panel. Artifact contents are browsed directly through `artifact.tar`.

### Hidden source files

The plugin does not filter dotfiles or hidden directories from `sources.tar` or
`artifact.tar`. MC's panel settings apply inside archives too: press **Alt-.**
(the default ShowHidden binding) or enable **Show hidden files** in MC's panel
options. A custom keymap may use a different shortcut.

To distinguish a panel setting from missing checkout contents, export the same
virtual archive outside MC and inspect its member names:

```sh
bstmc copyout /tmp/project.bstmc all/elements/app.bst/sources.tar /tmp/sources.tar
tar -tf /tmp/sources.tar
```

Use your bookmark and element path. If a file exists in the tar but is absent
from the panel, check MC's hidden-file setting and panel filters. If it is absent
from the tar, check the element's sources and `bst source checkout` output; a
checkout is not a copy of every file in the project directory.

## Downloads, caching, and refresh

Opening the project loads the graph. Inspection and exports happen only when
you open or copy a virtual file. Each archive exports the **entire selected
element**, without dependencies; large elements can take time and temporary
disk space even when you only want one file.

- `sources.tar` includes generated build scripts without executing them and may fetch missing sources. Open workspaces follow BuildStream's
  source-checkout semantics, so contents need not match an already built artifact.
- `artifact.tar` automatically runs `bst artifact pull --deps none` for an
  uncached element, using configured remotes and project options. Cached artifacts
  (including cached failed builds) skip the pull. Failure is reported and can be
  retried; the plugin does not build the element or run integration commands.
- Downloads remain in BuildStream's cache after MC closes. Temporary export
  staging is cleaned up after the operation; MC manages its own extracted VFS
  files. Abrupt termination can leave temporary files behind.

The VFS is read-only: editing, deleting, and uploading project files are not
supported. Do not edit nested archives, because changes cannot be written back.

MC caches listings and opened files. Unloaded virtual files have placeholder
sizes of zero. To refresh, leave the VFS and free it through MC's active VFS list,
or restart MC; Ctrl-r alone may reuse cached data. Queries are separate live
operations, not a consistent project snapshot.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `MC helper is missing`, broken, or outdated | Run `bst-mc --install-mc` from the intended installation and restart MC. Use `mc --datadir-info` to inspect the actual user directory. Back up an unrelated conflicting helper before replacing it manually. |
| `bst` is missing or the project cannot load | Run `command -v bst` and `bst -C /path/to/project show app.bst` in the same terminal. Fix plugins and project options in the BuildStream environment. |
| Enter opens bookmark JSON | Add the extension association above or use `cd ~/project.bstmc/bstmc://`. |
| MC displays old paths or files | Leave and free the VFS, or restart MC after updating the helper. |
| Artifact export fails | Check the artifact's availability and configured remotes. Retry after resolving the reported BuildStream error. |
| An old installation tries to install `bst-tree` | Update this checkout and reinstall `./contrib/mcplugin`; the current package is standalone. |

Diagnose the extfs protocol without starting MC:

```sh
bst-mc /path/to/project app.bst -o /tmp/project.bstmc
bstmc list /tmp/project.bstmc
bstmc copyout /tmp/project.bstmc all/elements/app.bst/build-commands.txt /tmp/commands.txt
```

Choose a fresh bookmark path if it already exists. `list` prints an extfs listing;
BuildStream failures are reported on stderr with a nonzero exit status. Normal
launcher/helper success returns 0, operational failures 1, and interruption 130;
invalid launcher arguments use argparse's exit status 2.

## Development

From the repository root, in a development virtualenv:

```sh
python -m pip install -e './contrib/mcplugin[test]'
python -m pytest -c contrib/mcplugin/pyproject.toml contrib/mcplugin/tests
# Opt in only with a working bst and buildbox-casd installation:
BST_MC_TEST_LIVE=1 python -m pytest -c contrib/mcplugin/pyproject.toml contrib/mcplugin/tests
```

The bundled `_bst.py` and `_graph.py` modules are adapted from bst-tree; package
installation does not read neighboring source directories. Tests cover command
arguments, graph navigation, junction paths, installation, exports, and errors.
Opt-in live tests also exercise automatic artifact pulls against a local server.
On macOS, the live tests may require a higher file-descriptor limit such as
`ulimit -n 4096` and an explicit Linux/aarch64 sandbox target.

See the [MC extfs protocol documentation](https://github.com/MidnightCommander/mc/blob/master/src/vfs/extfs/helpers/README)
for helper protocol details.
