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
from bst_tools_core.process import CommandRunner


def test_export_runner_writes_binary_file_and_enforces_timeout(tmp_path):
    import sys

    runner = CommandRunner()
    try:
        path = tmp_path / "data"
        with path.open("wb") as output:
            result = runner(
                [
                    sys.executable,
                    "-c",
                    "import sys;sys.stdout.buffer.write(b'\\x00ELF');sys.stderr.write('diagnostic')",
                ],
                stdout_file=output,
            )
        assert result.returncode == 0 and path.read_bytes() == b"\x00ELF" and result.stderr == "diagnostic"
        assert runner([sys.executable, "-c", "import time;time.sleep(10)"], timeout=0.05).timed_out
    finally:
        runner.cancel()
