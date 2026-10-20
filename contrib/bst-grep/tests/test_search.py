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
"""Exercise real ripgrep, rather than reproducing its matching implementation."""

import base64
import os
import shutil
from types import SimpleNamespace

import pytest

from bst_grep.search import rg_arguments, search, validate

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="Install ripgrep")


def options(pattern="needle", **kwargs):
    return SimpleNamespace(
        **{
            "pattern": pattern,
            "fixed_strings": False,
            "ignore_case": False,
            "word_regexp": False,
            "glob": [],
            "before_context": 0,
            "after_context": 0,
            **kwargs,
        }
    )


def test_globs_hidden_ignored_symlinks_and_context(tmp_path, monkeypatch):
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "main.c").write_text("before\nneedle\nafter\n")
    (tmp_path / ".ignore").write_text("*.c\n")
    (tmp_path / "other.py").write_text("needle\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "metadata.c").write_text("needle\n")
    outside = tmp_path.parent / "outside.c"
    outside.write_text("needle\n")
    (tmp_path / "link.c").symlink_to(outside)
    # An installed rg config must not change output format or follow links.
    config = tmp_path / "rg.config"
    config.write_text("--files\n--follow\n")
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(config))
    command = rg_arguments(options(glob=["*.c"], before_context=1, after_context=1))
    rows = list(search(tmp_path, command))
    assert [r["type"] for r in rows] == ["context", "match", "context"]
    assert [r["line"] for r in rows] == [1, 2, 3]
    assert {r["path"] for r in rows} == {".hidden/main.c"}
    assert rows[1]["column"] == 1


def test_literal_case_word_and_byte_columns(tmp_path):
    (tmp_path / "a file.c").write_text("ж x[A] x[A]tail\n", encoding="utf-8")
    rows = list(search(tmp_path, rg_arguments(options("X[A]", fixed_strings=True, ignore_case=True))))
    assert rows[0]["column"] == 4  # UTF-8 byte column, as in rg.
    assert rows[0]["submatches"] == [{"start": 3, "end": 7}, {"start": 8, "end": 12}]
    (tmp_path / "words.txt").write_text("needle needles\n")
    rows = list(search(tmp_path, rg_arguments(options(word_regexp=True))))
    assert rows[0]["submatches"] == [{"start": 0, "end": 6}]


def test_invalid_utf8_and_filename_are_lossless_in_json(tmp_path):
    name = os.fsdecode(b"strange-\xff\n.c")
    (tmp_path / name).write_bytes(b"\xff needle\n")
    rows = list(search(tmp_path, rg_arguments(options())))
    assert rows[0]["path"] == name
    assert base64.b64decode(rows[0]["path_base64"]) == os.fsencode(name)
    assert base64.b64decode(rows[0]["text_base64"]) == b"\xff needle\n"


def test_binary_no_match_and_exclude(tmp_path):
    (tmp_path / "data.bin").write_bytes(b"\x00needle\n")
    (tmp_path / "excluded.c").write_text("needle\n")
    assert list(search(tmp_path, rg_arguments(options(glob=["!excluded.c"])))) == []
    assert list(search(tmp_path, rg_arguments(options("absent")))) == []


@pytest.mark.parametrize("kwargs", [{"pattern": "["}, {"glob": ["["]}])
def test_validate_before_checkout(kwargs):
    with pytest.raises(ValueError):
        validate(rg_arguments(options(**kwargs)))


def test_valid_pattern_without_matches():
    validate(rg_arguments(options("--leading-dash")))


def test_streaming_search_cancellation(tmp_path):
    # Closing a consumer must terminate a running rg process and release handles.
    (tmp_path / "large.c").write_text("needle\n" * 50000)
    events = search(tmp_path, rg_arguments(options()))
    assert next(events)["type"] == "match"
    events.close()
    assert not list(tmp_path.glob("bst-grep-*"))
