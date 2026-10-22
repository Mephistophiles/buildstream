#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Discover the local cache using BuildStream 2 user configuration rules."""

import os
import re
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


def cas_directory(explicit=None):
    override = explicit or os.environ.get("BST_CAS_DIRECTORY")
    if override:
        return str(Path(override).expanduser().resolve())
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    cache_home = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    cachedir = str(Path(cache_home) / "buildstream")
    for name in ("buildstream2.conf", "buildstream.conf"):
        config = config_home / name
        if not config.exists():
            continue
        try:
            data = YAML(typ="safe").load(config)
            if data is None:
                data = {}
        except YAMLError as error:
            raise ValueError(f"Cannot read BuildStream configuration {config}: {error}") from error
        if not isinstance(data, dict):
            raise ValueError(f"Expected a mapping in {config}")
        cachedir = data.get("cachedir", cachedir)
        break
    if not isinstance(cachedir, str) or not cachedir or "\x00" in cachedir:
        raise ValueError("BuildStream cachedir must be a nonempty absolute path")
    # BuildStream initializes XDG defaults before expanding config paths.
    environment = dict(os.environ)
    for key, default in (("XDG_CACHE_HOME", cache_home), ("XDG_CONFIG_HOME", str(config_home)),
                         ("XDG_DATA_HOME", str(Path.home() / ".local/share"))):
        if not environment.get(key):
            environment[key] = default
    cachedir = re.sub(
        r"\$(?:\{([^}]+)\}|([A-Za-z_][A-Za-z_0-9]*))",
        lambda match: environment.get(match.group(1) or match.group(2), match.group(0)),
        os.path.expanduser(cachedir),
    )
    if not os.path.isabs(cachedir):
        raise ValueError("BuildStream cachedir must be absolute; set --cas-directory to override")
    return str(Path(cachedir) / "cas")
