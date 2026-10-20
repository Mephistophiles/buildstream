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
"""Own child processes, diagnostics and cancellation while streaming CLI output."""

from contextlib import contextmanager
import os
import signal
import subprocess
import tempfile


def stop(process):
    if process.poll() is not None:
        return

    # bst can spawn fetch workers and buildbox-casd. Terminate the whole group,
    # and wait before removing directories that those workers may still use.
    def send(sig):
        try:
            if os.name == "posix":
                os.killpg(process.pid, sig)
            elif sig == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        except ProcessLookupError:
            pass

    send(signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        send(signal.SIGKILL)
        process.wait()


@contextmanager
def child(arguments, *, cwd=None, binary=False):
    # A file avoids a full stderr pipe blocking a child while stdout is streamed.
    with tempfile.TemporaryFile(mode="w+b") as diagnostics:
        process = subprocess.Popen(
            arguments,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=diagnostics,
            start_new_session=os.name == "posix",
            **({} if binary else {"text": True, "encoding": "utf-8", "errors": "strict"}),
        )
        try:
            yield process, diagnostics
        finally:
            stop(process)
            process.stdout.close()


def diagnostic_text(diagnostics):
    diagnostics.seek(0)
    return diagnostics.read().decode("utf-8", errors="replace").strip()


def execute(arguments):
    with child(arguments) as (process, diagnostics):
        stdout, _ = process.communicate()
        if process.returncode:
            raise ValueError(diagnostic_text(diagnostics) or f"{arguments[0]} exited {process.returncode}")
        return stdout
