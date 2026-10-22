#!/usr/bin/env bash
# Install the checkout's standalone tools into one pipx-managed environment.
set -euo pipefail

usage() {
    cat <<'HELP'
Usage: install-bst-tools.sh [--headless] [--force]

Install bst-cas, bst-tree, bst-diff, bst-elf, bst-blame and bst-grep from
this checkout. All packages share the bst-cas pipx environment.

  --headless  Omit optional Textual/Rich dependencies on a fresh installation.
  --force     Reinstall packages from this checkout and replace app links.
  -h, --help  Show this help.

Requires pipx and Python 3.10+ (select Python with PIPX_DEFAULT_PYTHON).
BuildStream, its project plugins and external analyzers are installed separately.
After git pull, rerun with --force to update the installed checkout packages.
Use pipx uninstall bst-cas to remove the suite.
HELP
}

tui='[tui]'
force=()
for arg in "$@"; do
    case "$arg" in
        --headless) tui='' ;;
        --force) force=(--force) ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$arg" >&2; usage >&2; exit 2 ;;
    esac
done
command -v pipx >/dev/null || { echo 'pipx is required; install it first.' >&2; exit 1; }
contrib_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

# pipx parses --pip-args with shlex; quote the local requirement even if the
# checkout path contains spaces or apostrophes. Supply it during every consumer
# install because --force may also re-resolve/reinstall dependencies.
core_path="$contrib_dir/bst-tools-core"
core_arg="'${core_path//\'/\'\"\'\"\'}'"

# bst-cas has an entry point and no dependency on unpublished sibling packages.
pipx install "${force[@]}" "$contrib_dir/bst-cas"
# Install the unpublished library before resolving any consumers.
pipx inject "${force[@]}" bst-cas "$contrib_dir/bst-tools-core"
for package in bst-tree bst-diff bst-elf bst-blame bst-grep; do
    extras=''
    case "$package" in bst-tree|bst-diff) extras=$tui ;; esac
    pipx inject "${force[@]}" --include-apps --pip-args "$core_arg" bst-cas "$contrib_dir/$package$extras"
done
pipx list --include-injected
