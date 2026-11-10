# Migration from bst-tree

Keep the existing `bst-tree` CLI and TUI available during the migration. Develop
new workflows in the standalone commands. Retire the `bst-tree` executable
after its remaining workflows have replacements and users have a migration
path. The standalone commands can share Python code and snapshot formats.

## Current coverage and destination

| Workflow | Standalone commands | Status |
| --- | --- | --- |
| Capture a graph and input provenance | `bst-snapshot`, `bst-origin` | Available. Verify existing captures and CI scripts before retiring the legacy invocations. |
| Compare closure membership and declared edge changes | `bst-closure-diff` | Standalone TUI, text and JSON available. Validate the TUI on large project snapshots. |
| Compare graph structure and explain key changes | `bst-graph-diff`, `bst-key-explain` | Headless entry points available. Both can still use the legacy diff inspector for TUI. |
| Inspect graph paths and select rebuild candidates | `bst-rdeps`, `bst-path`, `bst-paths`, `bst-chains`, `bst-rebuild-select` | Available. Rebuild selection reports candidates and does not execute a build. |
| Inspect inputs, sources, artifacts and sizes | `bst-inputs`, `bst-source-files`, `bst-artifact-files`, `bst-du` | Available. Validate live project behavior and existing integrations. |
| Browse and print a dependency tree | Replacement to be designed | Still served by `bst-tree browse` and `bst-tree tree`. |

## 1. Stabilize standalone closure comparison

- [x] Install `bst-closure-diff` as an independent executable.
- [x] Launch a dedicated TUI from `bst-closure-diff --tui` with target, scope,
  search, closure effects and exact declared edge changes.
- [x] Keep the legacy TUI and the existing headless text/JSON comparison paths.
- [x] Cover transitions between `build/run` and `build` in both directions,
  replaced dependencies and targets present on only one side.
- [ ] Try both views on real project captures with many targets and changes.
  Check narrow terminals, long element names and latency when searching or
  switching targets.

## 2. Replace the remaining interactive workflows

- [ ] Define standalone workflows for printing a tree and browsing a live
  project or a snapshot. Cover dependency navigation, reverse dependencies,
  paths and links to element inspection.
- [ ] Give graph diff and key explanation dedicated TUI entry paths. Preserve
  section diffs, root-target context, provenance and YAML history where available.
- [ ] Review shared graph semantics before switching callers. Cover build scope
  for multi-root paths and distinguish typed values such as `false` and `0` in
  structural comparisons.
- [ ] Document the mapping from legacy invocations and formats to standalone
  commands. Verify each replacement through both pipx and normal Python
  installation.

## 3. Retire the compatibility entry point

- [ ] Verify scripts and CI consumers have switched. Exercise live projects,
  old snapshots, headless pipelines, and the TUI replacements.
- [ ] Announce a transition period with examples for the remaining legacy calls.
- [ ] Remove the `bst-tree` executable and legacy-only dispatch after the
  transition. Review the Python package name separately from CLI removal.
