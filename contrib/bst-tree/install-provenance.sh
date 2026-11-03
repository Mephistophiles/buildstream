#!/usr/bin/env bash
# Install BuildStream and the checkout's provenance extensions privately.
set -euo pipefail
runtime="${XDG_DATA_HOME:-$HOME/.local/share}/bst-tree/provenance"
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mkdir -p -- "$runtime"
if [[ ! -x "$runtime/venv/bin/python" ]]; then
    "${PIPX_DEFAULT_PYTHON:-python3}" -m venv "$runtime/venv"
fi
python="$runtime/venv/bin/python"
"$python" -I -m pip install 'BuildStream==2.8.0' 'Cython>=3.1' setuptools \
    'buildstream-plugins==2.7.0' 'buildstream-plugins-community==2.3.0' 'requests==2.33.1'
overlay=$(mktemp -d "$runtime/overlay.XXXXXXXX")
if ! "$python" -I "$source_dir/build_provenance.py" "$overlay" "$runtime/bst-provenance"; then
    rm -rf -- "$overlay"
    exit 1
fi
