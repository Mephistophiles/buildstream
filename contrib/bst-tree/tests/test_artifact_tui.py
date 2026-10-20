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
import asyncio
import io
from pathlib import Path
import tarfile
import threading

import pytest
from textual.widgets import DataTable, Input, RichLog, Select, Tabs

from bst_tree.artifacts import ArtifactComparison
from bst_tree.artifact_process import Cancelled, Execution
from bst_tree.artifact_tui import ArtifactDiffApp


def pair(tmp_path):
    result = []
    for name, text, elf in [("old", b"old\n", b"\x7fELF\x00old"), ("new", b"new\n", b"\x7fELF\x00new")]:
        path = tmp_path / (name + ".tar")
        with tarfile.open(path, "w") as bundle:
            for filename, data in [("etc/config", text), ("lib/lib[1].so", elf)]:
                member = tarfile.TarInfo(filename)
                member.size = len(data)
                member.mode = 0o644
                bundle.addfile(member, io.BytesIO(data))
        result.append(str(path))
    return result


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        return Execution(
            4 if Path(args[0]).name == "abidiff" else 0 if Path(args[0]).name == "bloaty" else 1,
            "detailed differences",
        )

    def cancel(self):
        pass


@pytest.mark.parametrize("size", [(150, 40), (80, 30)])
async def test_artifact_tui_analysis_navigation_search_and_cleanup(tmp_path, monkeypatch, size):
    monkeypatch.setattr("bst_tree.artifacts.shutil.which", lambda name: "/usr/bin/" + name)
    runner = Runner()
    session = ArtifactComparison(*pair(tmp_path), archives=True, runner=runner)
    app = ArtifactDiffApp(session)
    async with app.run_test(size=size) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.ready and app.selected_path == "etc/config"
        assert app.query_one(RichLog).lines
        await pilot.press("d")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert session.analyses["diffoscope", "etc/config"]["status"] == "changed"
        assert app.query_one(Select).value == "diffoscope"
        app.query_one(Tabs).active = "af-elf"
        await pilot.pause()
        assert app.selected_path == "lib/lib[1].so"
        await pilot.press("a")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one(Select).value == "abidiff"
        assert session.analyses["abidiff", "lib/lib[1].so"]["status"] == "changed"
        await pilot.press("b")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one(Select).value == "bloaty"
        assert session.analyses["bloaty", "lib/lib[1].so"]["status"] == "analyzed"
        await pilot.press("/")
        await pilot.press("q")
        assert app.query_one(Input).value == "q" and app.is_running
        await pilot.press("escape")
        app.query_one(Input).value = ""
        await pilot.pause()
        await pilot.press("D")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one(Select).value == "whole"
        assert ("diffoscope", None) in session.analyses
        await pilot.press("q")
    assert not session.root.exists()
    assert len(runner.calls) == 4


async def test_artifact_tui_missing_analyzer_is_retryable(tmp_path, monkeypatch):
    session = ArtifactComparison(*pair(tmp_path), archives=True, runner=Runner())
    monkeypatch.setattr("bst_tree.artifacts.shutil.which", lambda name: None)
    app = ArtifactDiffApp(session)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("d")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert session.analyses["diffoscope", "etc/config"]["status"] == "unavailable"
        monkeypatch.setattr("bst_tree.artifacts.shutil.which", lambda name: "/usr/bin/" + name)
        await pilot.press("d")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert session.analyses["diffoscope", "etc/config"]["status"] == "changed"


async def test_artifact_tui_quit_cancels_startup_and_owns_cleanup(tmp_path):
    session = ArtifactComparison(*pair(tmp_path), archives=True)
    started = threading.Event()

    def loader(progress):
        started.set()
        session.cancelled.wait(3)
        if session.cancelled.is_set():
            raise Cancelled("cancelled")

    app = ArtifactDiffApp(session, loader=loader)
    async with app.run_test() as pilot:
        assert await asyncio.to_thread(started.wait, 2)
        await pilot.press("q")
        await pilot.pause()
        assert app.return_value == 130
    assert not session.root.exists()


async def test_late_analyzer_result_does_not_overwrite_new_selection(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class BlockingRunner(Runner):
        def __call__(self, args, **kwargs):
            started.set()
            release.wait(3)
            return Execution(1, "file detail")

    monkeypatch.setattr("bst_tree.artifacts.shutil.which", lambda name: "/usr/bin/" + name)
    session = ArtifactComparison(*pair(tmp_path), archives=True, runner=BlockingRunner())
    app = ArtifactDiffApp(session)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("d")
        assert await asyncio.to_thread(started.wait, 2)
        app.query_one(DataTable).move_cursor(row=1)
        await pilot.pause()
        assert app.selected_path == "lib/lib[1].so"
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one(Select).value == "overview"
        assert ("diffoscope", "etc/config") in session.analyses
