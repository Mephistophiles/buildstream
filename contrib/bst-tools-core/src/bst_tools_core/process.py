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

from contextlib import contextmanager
from dataclasses import dataclass
import os
import signal
import subprocess
import threading
import tempfile
import sys


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

    def __call__(self, arguments, *, timeout=120, limit=1024 * 1024, stdout_file=None, cwd=None):
        with self.serial:
            with self.lock:
                if self.cancelled.is_set():
                    raise Cancelled("Command cancelled")
                process = subprocess.Popen(
                    arguments,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file if stdout_file is not None else subprocess.PIPE,
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
                if stream is not None
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
                raise Cancelled("Command cancelled")
            return Execution(
                process.returncode, *(b.decode("utf-8", errors="replace") for b in buffers), any(truncated), timed_out
            )


class CliRunner:
    """Bounded text adapter for public bst calls, usable from CLI or TUI."""

    def __init__(self, timeout=120, limit=32 * 1024 * 1024, *, quiet=False):
        self.runner = CommandRunner()
        self.timeout, self.limit = timeout, limit
        self.quiet = quiet
        self.diagnostics = []

    def __call__(self, arguments, **kwargs):
        result = self.runner(arguments, timeout=self.timeout, limit=self.limit, cwd=kwargs.get("cwd"))
        if result.timed_out:
            raise ValueError("BuildStream command timed out")
        if result.truncated:
            raise ValueError("BuildStream output exceeded --output-limit; increase it and retry")
        if result.returncode:
            raise ValueError(result.stderr.strip() or f"{arguments[0]} exited {result.returncode}")
        if result.stderr:
            self.diagnostics.append(result.stderr.strip())
            if not self.quiet:
                print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
        return subprocess.CompletedProcess(arguments, result.returncode, result.stdout)

    def cancel(self):
        self.runner.cancel()


def execute(arguments):
    return CliRunner(quiet=True)(arguments).stdout


def stop(process):
    if process.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            kill_group(process)
            process.wait()
    # Reap descendants even if their group leader has already exited.
    kill_group(process)


@contextmanager
def child(arguments, *, cwd=None, binary=False):
    """Stream unbounded stdout; diagnostics go to disk, never a blocking pipe."""
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
