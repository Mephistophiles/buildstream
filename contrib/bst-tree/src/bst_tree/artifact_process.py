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
"""Bounded analyzer output and cancellable process groups, independent of Textual."""

from dataclasses import dataclass
import os
import signal
import subprocess
import threading


class Cancelled(ValueError):
    pass


@dataclass
class Execution:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    timed_out: bool = False


def kill_group(process):
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass


class CommandRunner:
    """One command at a time; cancellation also stops helpers spawned by analyzers."""

    def __init__(self, cancelled=None):
        self.cancelled = cancelled if cancelled is not None else threading.Event()
        self.lock = threading.Lock()
        self.serial = threading.Lock()
        self.process = None

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            if self.process is not None:
                kill_group(self.process)

    def __call__(self, arguments, *, timeout=120, limit=1024 * 1024):
        with self.serial:
            with self.lock:
                if self.cancelled.is_set():
                    raise Cancelled("Artifact comparison cancelled")
                process = subprocess.Popen(
                    arguments,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=os.name == "posix",
                )
                self.process = process
            buffers = [bytearray(), bytearray()]
            truncated = [False, False]

            def collect(stream, index):
                try:
                    while chunk := stream.read(16384):
                        remaining = max(0, limit - len(buffers[index]))
                        buffers[index].extend(chunk[:remaining])
                        truncated[index] |= len(chunk) > remaining
                finally:
                    stream.close()

            threads = [
                threading.Thread(target=collect, args=(stream, index), daemon=True)
                for index, stream in enumerate((process.stdout, process.stderr))
            ]
            for thread in threads:
                thread.start()
            timed_out = False
            try:
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    kill_group(process)
                    process.wait()
            finally:
                # A helper retaining a pipe must not outlive its owning command.
                kill_group(process)
                process.wait()
                for thread in threads:
                    thread.join()
                with self.lock:
                    self.process = None
            if self.cancelled.is_set():
                raise Cancelled("Artifact comparison cancelled")
            return Execution(
                process.returncode, *(b.decode("utf-8", errors="replace") for b in buffers), any(truncated), timed_out
            )
