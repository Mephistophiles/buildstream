# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Register and verify the helper in the user directory reported by MC."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from . import __version__


MARKER = "# Managed by bst-mc --install-mc"


def helper_path():
    if not shutil.which("mc"):
        raise ValueError("mc is not on PATH; install Midnight Commander with extfs support")
    result = subprocess.run(
        ["mc", "--datadir-info"], capture_output=True, text=True,
        env={**os.environ, "LC_ALL": "C"}, check=False,
    )
    if result.returncode:
        raise ValueError(f"mc --datadir-info failed: {result.stderr.strip()}")
    _, separator, user_data = result.stdout.partition("[User data]")
    if separator:
        for line in user_data.splitlines():
            key, colon, value = line.strip().partition(":")
            if colon and key == "extfs.d" and Path(value.strip()).is_absolute():
                return Path(value.strip()) / "bstmc"
    raise ValueError("MC did not report a user extfs.d directory; check mc --datadir-info and extfs support")


def install_helper():
    destination = helper_path()
    # Use the current venv's interpreter, not a possibly unrelated bstmc on
    # PATH. Do not resolve the Python symlink: that would escape the pipx venv.
    content = f'#!/bin/sh\n{MARKER}\nexec {shlex.quote(sys.executable)} -m bst_mc.vfs "$@"\n'
    if destination.exists() or destination.is_symlink():
        try:
            with destination.open(encoding="utf-8") as stream:
                previous = stream.read(16384)
        except (OSError, UnicodeError):
            previous = ""
        if MARKER not in previous and "from bst_mc.vfs import main" not in previous:
            raise ValueError(f"Refusing to replace an unrecognized helper: {destination}; move it aside first")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.chmod(0o755)
        temporary.replace(destination)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return destination


def check_helper():
    helper = helper_path()
    repair = "Run bst-mc --install-mc using this installation, then restart MC."
    if not helper.is_file() or not os.access(helper, os.X_OK):
        raise ValueError(f"MC helper is missing or not executable: {helper}. {repair}")
    try:
        result = subprocess.run([str(helper), "--version"], capture_output=True, text=True, check=False)
    except OSError as error:
        raise ValueError(f"Cannot execute MC helper: {helper}. {repair}\n{error}") from error
    if result.returncode or result.stdout.strip() != f"bst-mc {__version__}":
        raise ValueError(f"MC helper is broken or outdated: {helper}. {repair}\n{result.stderr.strip()}")
    if not shutil.which("bst"):
        raise ValueError("bst is not on PATH; install BuildStream separately (e.g. pipx install buildstream)")
    return helper


def check_project(helper, bookmark):
    # Exercise the actual installed helper before opening MC. This catches bad
    # targets, missing project plugins and broken environments in the terminal.
    result = subprocess.run([str(helper), "list", str(bookmark)], stdout=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise ValueError("Cannot open the BuildStream project; see diagnostics above. MC was not started.")
