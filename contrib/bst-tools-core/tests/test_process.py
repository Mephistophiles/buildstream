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
import sys
import threading
import time

import pytest

from bst_tools_core.process import Cancelled, CommandRunner


def test_bounded_output_and_nonzero_are_preserved():
    runner = CommandRunner()
    result = runner(
        [sys.executable, "-c", "import sys; print('x'*100000); print('y'*100000,file=sys.stderr); sys.exit(4)"],
        limit=256,
    )
    assert result.returncode == 4 and result.truncated
    assert len(result.stdout) == len(result.stderr) == 256


def test_timeout_does_not_prevent_a_retry():
    runner = CommandRunner()
    assert runner([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05).timed_out
    assert runner([sys.executable, "-c", "print('ok')"]).stdout.strip() == "ok"


def test_cancellation_stops_an_active_command():
    runner = CommandRunner()
    errors = []

    def work():
        try:
            runner([sys.executable, "-c", "import time; time.sleep(30)"])
        except Cancelled as e:
            errors.append(e)

    thread = threading.Thread(target=work)
    thread.start()
    deadline = time.monotonic() + 3
    while runner.process is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.process is not None
    runner.cancel()
    thread.join(timeout=3)
    assert not thread.is_alive() and errors
    with pytest.raises(Cancelled):
        runner([sys.executable, "-c", "pass"])
