#!/usr/bin/env bash
# Install BuildStream and the checkout's provenance extensions privately.
set -euo pipefail
runtime="${XDG_DATA_HOME:-$HOME/.local/share}/bst-tree/provenance"
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
base_python="${PIPX_DEFAULT_PYTHON:-python3}"
venv="$runtime/venv"
python="$venv/bin/python"
mkdir -p -- "$runtime"

# Keep the lock in the shell while updating the venv and publishing the launcher.
exec 9>>"$runtime/.install.lock"
lock_python=$(command -v "$base_python" || printf '%s\n' "$python")
"$lock_python" -I -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX)'

if ! "$python" -I -c 'import sys; sys.exit(sys.prefix == sys.base_prefix)' >/dev/null 2>&1; then
    # venv leaves existing interpreter symlinks in place, even when dangling.
    if [[ -e "$venv" || -L "$venv" ]]; then
        backup=$(mktemp -d "$runtime/venv-backup.XXXXXXXX")
        mv -- "$venv" "$backup/"
        printf 'Moved unusable provenance environment to %s\n' "$backup/venv" >&2
    fi
    "$base_python" -m venv "$venv"
fi
if ! "$python" -I -m pip --version >/dev/null 2>&1; then
    "$python" -I -m ensurepip --upgrade
fi
"$python" -I -m pip install 'BuildStream==2.8.0' 'Cython>=3.1' setuptools \
    'buildstream-plugins==2.7.0' 'buildstream-plugins-community==2.3.0' 'requests==2.33.1'
overlay=$(mktemp -d "$runtime/overlay.XXXXXXXX")
if ! "$python" -I "$source_dir/build_provenance.py" "$overlay" "$runtime/bst-provenance"; then
    rm -rf -- "$overlay"
    exit 1
fi
