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
"""Build the changed node extension against a released BuildStream runtime.

This intentionally compiles real Cython; copying Python files cannot exercise
composition history. Node's fields and Cython method ABI remain unchanged.
Usage: python build_provenance.py /absolute/overlay-root [launcher-path]
"""

from pathlib import Path
import os
import shlex
import shutil
import sys
import tempfile

import buildstream
from Cython.Build import cythonize
from setuptools import Extension, setup

os.environ.setdefault("CC", shutil.which("cc") or "gcc")

repository = Path(__file__).resolve().parents[2]
installed = Path(buildstream.__file__).parent
destination = Path(sys.argv[1]).resolve()
package = destination / "buildstream"
package.mkdir(parents=True, exist_ok=True)

for source in installed.rglob("*"):
    relative = source.relative_to(installed)
    if "__pycache__" in relative.parts:
        continue
    target = package / relative
    if source.is_dir():
        target.mkdir(exist_ok=True)
    elif not target.exists():
        target.symlink_to(source)

modules = (
    "_composition_history.py",
    "_input_provenance.py",
    "element.py",
    "_context.py",
    "_frontend/cli.py",
    "_frontend/app.py",
    "_options/optionpool.py",
)
for name in modules:
    target = package / name
    target.unlink(missing_ok=True)
    shutil.copyfile(repository / "src/buildstream" / name, target)
for target in package.glob("node.*.so"):
    target.unlink()

with tempfile.TemporaryDirectory(prefix="bst-origin-cython-") as temporary:
    os.chdir(temporary)  # Do not pick up the repository's package setup metadata.
    setup(
        name="bst-origin-native-test",
        ext_modules=cythonize(
            [Extension("buildstream.node", [str(repository / "src/buildstream/node.pyx")])],
            include_path=[str(repository / "src")],
            build_dir=temporary,
            compiler_directives={"language_level": 3},
        ),
        script_args=["build_ext", "--build-lib", str(destination), "--build-temp", temporary],
    )

if len(sys.argv) == 3:
    # Publish only after native compilation succeeds. Existing queries retain
    # their own overlay path, including any BuildStream worker subprocesses.
    launcher = Path(sys.argv[2])
    with tempfile.NamedTemporaryFile("w", dir=launcher.parent, delete=False) as wrapper:
        wrapper.write("#!/bin/sh\n")
        wrapper.write(f'export PYTHONPATH={shlex.quote(str(destination))}${{PYTHONPATH:+":$PYTHONPATH"}}\n')
        wrapper.write(f'exec {shlex.quote(str(Path(sys.executable).with_name("bst")))} "$@"\n')
    os.chmod(wrapper.name, 0o755)
    os.replace(wrapper.name, launcher)

print("Origin runtime overlay: " + str(destination))
