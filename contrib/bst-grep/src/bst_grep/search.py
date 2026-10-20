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
"""Stream ripgrep's structured matches, retaining byte offsets and unusual names."""

import base64
import json
import os
from pathlib import PurePosixPath

from bst_tools_core.process import child, diagnostic_text


def rg_arguments(args):
    command = [
        "rg",
        "--no-config",
        "--json",
        "--color",
        "never",
        "--hidden",
        "--no-ignore",
        "--no-follow",
        "--sort",
        "path",
        "--encoding",
        "none",
    ]
    if args.fixed_strings:
        command.append("--fixed-strings")
    if args.ignore_case:
        command.append("--ignore-case")
    if args.word_regexp:
        command.append("--word-regexp")
    for glob in args.glob:
        command += ["--glob", glob]
    # Keep administrative directories excluded even if user globs include them.
    command += ["--glob", "!.git", "--glob", "!.hg", "--glob", "!.svn"]
    command += ["--before-context", str(args.before_context), "--after-context", str(args.after_context)]
    command += ["--regexp", args.pattern, "--", "."]
    return command


def validate(command):
    # Validate regex/globs before project loading or a potentially large checkout.
    # --files doesn't compile regexes; an empty stdin search does.
    validation = command[:-2] + ["-"]
    with child(validation) as (process, diagnostics):
        process.communicate()
        if process.returncode not in (0, 1):
            raise ValueError(diagnostic_text(diagnostics) or "Invalid ripgrep search")


def data_bytes(value):
    if "text" in value:
        return value["text"].encode("utf-8")
    return base64.b64decode(value["bytes"], validate=True)


def search(root, command):
    with child(command, cwd=root) as (process, diagnostics):
        for line in process.stdout:
            event = json.loads(line)
            if event["type"] not in ("match", "context"):
                continue
            data = event["data"]
            raw_path = data_bytes(data["path"])
            path = os.fsdecode(raw_path)
            if path.startswith("./"):
                path = path[2:]
            logical = PurePosixPath(path)
            if logical.is_absolute() or ".." in logical.parts:
                raise ValueError("ripgrep returned a path outside the search tree")
            raw_text = data_bytes(data["lines"])
            record = {
                "type": event["type"],
                "path": path,
                "line": data["line_number"],
                "column": data["submatches"][0]["start"] + 1 if event["type"] == "match" else None,
                "text": raw_text.decode("utf-8", errors="replace").removesuffix("\n"),
                "submatches": [{"start": match["start"], "end": match["end"]} for match in data["submatches"]],
            }
            if "bytes" in data["lines"]:
                record["text_base64"] = data["lines"]["bytes"]
            if "bytes" in data["path"]:
                record["path_base64"] = base64.b64encode(os.fsencode(path)).decode("ascii")
            yield record
        process.wait()
        if process.returncode not in (0, 1):
            raise ValueError(diagnostic_text(diagnostics) or f"ripgrep exited {process.returncode}")
