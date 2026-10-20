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
import io
import json
from pathlib import Path
import tarfile
import threading

import pytest

from bst_tools_core.process import Cancelled, Execution
from bst_diff.artifacts import Archive, ArtifactComparison, has_analysis_errors, has_artifact_changes, render_artifacts


def archive(path, entries):
    with tarfile.open(path, "w") as bundle:
        for name, value in entries:
            member = tarfile.TarInfo(name)
            member.mode = 0o644
            if isinstance(value, bytes):
                member.size = len(value)
                bundle.addfile(member, io.BytesIO(value))
            else:
                for key, item in value.items():
                    setattr(member, key, item)
                bundle.addfile(member)
    return path


def pair(tmp_path):
    old = archive(
        tmp_path / "old.tar",
        [
            ("etc/config", b"old\n"),
            ("lib/libx.so", b"\x7fELF\x00old"),
            ("bin/tool", b"same"),
            ("link", {"type": tarfile.SYMTYPE, "linkname": "/outside"}),
            ("hard", {"type": tarfile.LNKTYPE, "linkname": "bin/tool"}),
            ("gone", b"old"),
        ],
    )
    new = archive(
        tmp_path / "new.tar",
        [
            ("etc/config", b"new\n"),
            ("lib/libx.so", b"\x7fELF\x00new"),
            ("bin/tool", b"same"),
            ("link", {"type": tarfile.SYMTYPE, "linkname": "/elsewhere"}),
            ("hard", {"type": tarfile.LNKTYPE, "linkname": "bin/tool"}),
            ("added", b"new"),
        ],
    )
    return old, new


class FakeRunner:
    def __init__(self, code=0, stdout="report", stderr="", timed_out=False):
        self.result = Execution(code, stdout, stderr, False, timed_out)
        self.calls = []
        self.cancelled = False

    def __call__(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))
        if "--html" in arguments and self.result.returncode in (0, 1):
            Path(arguments[arguments.index("--html") + 1]).write_text("<html>report</html>")
        return self.result

    def cancel(self):
        self.cancelled = True


def test_manifest_content_metadata_and_hardlinks(tmp_path):
    old, new = pair(tmp_path)
    session = ArtifactComparison(str(old), str(new), archives=True)
    try:
        session.load()
        report = session.report()
        assert report["summary"] == {"added": 1, "removed": 1, "modified": 3, "unchanged": 0}
        assert report["elf_pairs"] == ["lib/libx.so"]
        assert report["changes"][2]["path"] == "gone"
        assert next(r for r in report["changes"] if r["path"] == "link")["fields"] == ["target"]
        assert "-old" in session.text_diff("etc/config") and "+new" in session.text_diff("etc/config")
        assert "Binary file" in session.text_diff("lib/libx.so")
        assert "not followed" in session.text_diff("link")
        assert has_artifact_changes(report)
        assert not has_analysis_errors(report)
        assert not (session.root / "before").exists()
        assert not (session.root / "outside").exists()
    finally:
        session.close()
    assert not session.root.exists()


@pytest.mark.parametrize(
    "entries",
    [
        [("../escape", b"x")],
        [("/absolute", b"x")],
        [("a/../escape", b"x")],
        [("a", b"x"), ("./a", b"y")],
        [("a", {"type": tarfile.SYMTYPE, "linkname": "/outside"}), ("a/child", b"x")],
        [("a", {"type": tarfile.LNKTYPE, "linkname": "b"}), ("b", {"type": tarfile.LNKTYPE, "linkname": "a"})],
        [("a", {"type": tarfile.LNKTYPE, "linkname": "/outside"})],
    ],
)
def test_ambiguous_or_escaping_archive_paths_are_rejected(tmp_path, entries):
    path = archive(tmp_path / "bad.tar", entries)
    with pytest.raises(ValueError):
        Archive(path, threading.Event()).scan()
    assert not (tmp_path / "escape").exists()


def test_mode_owner_device_and_timestamp_semantics(tmp_path):
    old = archive(
        tmp_path / "a.tar",
        [("dir", {"type": tarfile.DIRTYPE, "mode": 0o755}), ("fifo", {"type": tarfile.FIFOTYPE}), ("file", b"same")],
    )
    new = archive(
        tmp_path / "b.tar",
        [
            ("dir", {"type": tarfile.DIRTYPE, "mode": 0o700, "uid": 123}),
            ("fifo", {"type": tarfile.FIFOTYPE}),
            ("file", b"same"),
        ],
    )
    session = ArtifactComparison(str(old), str(new), archives=True)
    try:
        session.load()
        assert session.records[0]["fields"] == ["mode", "uid"]
        assert "not followed" in session.text_diff("fifo")
    finally:
        session.close()


def test_stale_archive_and_bounded_unicode_previews(tmp_path):
    path = archive(tmp_path / "a.tar", [("unicode", ("a" * 15 + "я").encode())])
    indexed = Archive(path, threading.Event()).scan()
    text, limited = indexed.preview("unicode", 16)
    assert text == "a" * 15 and limited
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        indexed.preview("unicode")


def test_partial_tail_and_final_newline_are_explicit(tmp_path):
    a = archive(tmp_path / "a.tar", [("file", b"abc")])
    b = archive(tmp_path / "b.tar", [("file", b"abc\n")])
    session = ArtifactComparison(str(a), str(b), archives=True)
    try:
        session.load()
        assert "no final newline" in session.text_diff("file")
    finally:
        session.close()


@pytest.mark.parametrize(
    "code,status",
    [
        (0, "no_reported_change"),
        (4, "changed"),
        (12, "incompatible"),
        (1, "error"),
        (3, "error"),
        (5, "error"),
        (8, "error"),
        (-9, "error"),
    ],
)
def test_abidiff_exit_bits_are_not_generic_command_failures(tmp_path, monkeypatch, code, status):
    old, new = pair(tmp_path)
    runner = FakeRunner(code)
    monkeypatch.setattr("bst_diff.artifacts.shutil.which", lambda name: "/usr/bin/" + name)
    session = ArtifactComparison(
        str(old),
        str(new),
        archives=True,
        runner=runner,
        debug_old=["/debug/old"],
        debug_new=["/debug/new"],
        suppressions=["/rules.abignore"],
    )
    try:
        session.load()
        result = session.analyze("abidiff", "lib/libx.so")
        assert result["status"] == status
        assert result["exit_code"] == code
        args = runner.calls[0][0]
        assert "--no-default-suppression" in args and "--debug-info-dir1" in args and "--debug-info-dir2" in args
        assert "--suppressions" in args
        assert Path(args[-2]).read_bytes() == b"\x7fELF\x00old"
        assert Path(args[-2]).stat().st_mode & 0o111 == 0
        assert not (session.root / "before/link").exists()
        assert result["command"][-2:] == ["before/lib/libx.so", "after/lib/libx.so"]
    finally:
        session.close()


@pytest.mark.parametrize("code,status", [(0, "no_reported_change"), (1, "changed"), (2, "error"), (-9, "error")])
def test_diffoscope_status_and_atomic_html(tmp_path, monkeypatch, code, status):
    old, new = pair(tmp_path)
    runner = FakeRunner(code)
    monkeypatch.setattr("bst_diff.artifacts.shutil.which", lambda name: "/usr/bin/" + name)
    output = tmp_path / "report.html"
    output.write_text("existing")
    session = ArtifactComparison(str(old), str(new), archives=True, runner=runner)
    try:
        session.load()
        result = session.analyze("diffoscope", html=output)
        assert result["status"] == status
        assert "--max-text-report-size" in runner.calls[0][0]
        assert output.read_text() == ("<html>report</html>" if code in (0, 1) else "existing")
    finally:
        session.close()


def test_missing_tools_timeout_retry_and_path_filter(tmp_path, monkeypatch):
    old, new = pair(tmp_path)
    monkeypatch.setattr("bst_diff.artifacts.shutil.which", lambda name: None)
    runner = FakeRunner(0, timed_out=True)
    session = ArtifactComparison(str(old), str(new), archives=True, paths=["lib/libx.so"], runner=runner)
    try:
        session.load()
        assert len(session.records) == 1
        assert session.analyze("abidiff", "lib/libx.so")["status"] == "unavailable"
        assert not runner.calls
        monkeypatch.setattr("bst_diff.artifacts.shutil.which", lambda name: "/usr/bin/" + name)
        assert session.analyze("abidiff", "lib/libx.so")["status"] == "timeout"
        runner.result = Execution(0, "")
        assert session.analyze("abidiff", "lib/libx.so")["status"] == "no_reported_change"
        with pytest.raises(ValueError, match="ELF"):
            session.analyze("abidiff", "etc/config")
        with pytest.raises(ValueError, match="regular"):
            session.analyze("diffoscope", "link")
    finally:
        session.close()


def test_exports_use_public_cli_without_dependencies_or_integration(tmp_path):
    old, new = pair(tmp_path)

    class ExportRunner(FakeRunner):
        def __call__(self, args, **kwargs):
            self.calls.append((args, kwargs))
            import shutil

            shutil.copyfile(old if args[-1] == "old/ref/key" else new, args[args.index("--tar") + 1])
            return Execution(0)

    runner = ExportRunner()
    session = ArtifactComparison(
        "old/ref/key", "new.bst", directory="/project", options=[("arch", "x86-64")], runner=runner
    )
    try:
        session.load()
        assert len(runner.calls) == 2
        for args, _ in runner.calls:
            assert args[:7] == ["bst", "--no-colors", "--strict", "-C", "/project", "--option", "arch"]
            assert args[args.index("--deps") + 1] == "none"
            assert "--no-integrate" in args
            assert "build" not in args
    finally:
        session.close()


def test_cancel_before_loading_releases_temporaries(tmp_path):
    old, new = pair(tmp_path)
    session = ArtifactComparison(str(old), str(new), archives=True)
    session.close()
    with pytest.raises(Cancelled):
        session.load()
    assert not session.root.exists()


@pytest.mark.parametrize("code,status", [(0, "analyzed"), (1, "error"), (-9, "error")])
def test_bloaty_diff_direction_and_status(tmp_path, monkeypatch, code, status):
    monkeypatch.setattr("bst_diff.artifacts.shutil.which", lambda name: "/usr/bin/" + name)
    runner = FakeRunner(code=code)
    session = ArtifactComparison(
        *(str(p) for p in pair(tmp_path)), archives=True, runner=runner, bloaty_sources="sections,compileunits"
    )
    try:
        session.load()
        result = session.analyze("bloaty", "lib/libx.so")
        assert result["status"] == status
        argv = runner.calls[0][0]
        assert argv[argv.index("-d") + 1] == "sections,compileunits"
        assert "/after/" in argv[-3] and argv[-2] == "--" and "/before/" in argv[-1]
        with pytest.raises(ValueError):
            session.analyze("bloaty", "etc/config")
    finally:
        session.close()
