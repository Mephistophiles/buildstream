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
"""Subprocess ownership with real processes and potentially large diagnostics."""

import sys

import pytest

from bst_grep.process import child, execute


def test_stderr_cannot_deadlock_streaming_stdout():
    output = execute([sys.executable, "-c", "import sys; sys.stderr.write('x' * 200000); print('done')"])
    assert output == "done\n"


def test_child_stopped_when_consumer_leaves():
    with child([sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"]) as (process, _):
        assert process.stdout.readline() == "ready\n"
    assert process.poll() is not None
    assert process.stdout.closed


def test_failure_preserves_diagnostics():
    with pytest.raises(ValueError, match="failed fixture"):
        execute([sys.executable, "-c", "import sys; sys.stderr.write('failed fixture'); sys.exit(7)"])
