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
"""Small fixtures for the documented human-readable BuildStream CLI contract."""


def listing(names, entries, *, long=False):
    chunks = []
    for name in names:
        chunks.append("  " + name.rsplit(":", 1)[-1] + ":")
        rows = entries.get(name, [])
        if not rows:
            chunks.append("\tThis element has no associated artifacts")
        for path, kind, size, target in rows:
            if long:
                mode = {"reg": "-rw-r--r--", "exe": "-rwxr-xr-x", "dir": "drwxr-xr-x", "link": "lrwxrwxrwx"}[kind]
                row = f"{mode}  {kind:<7}{str(size):<11} {path}"
                if kind == "link":
                    row += " -> " + target
            else:
                row = path
            chunks.append("\t" + row)
    return "\n".join(chunks) + "\n"


def framed(args, records):
    template = args[args.index("--format") + 1]
    output = []
    for record in records:
        row = template
        for field, value in record.items():
            row = row.replace("%{" + field + "}", value)
        output.append(row)
    return "".join(output)


def node(state="cached", *, kind="import", build=(), run=()):
    return {"kind": kind, "state": state, "full-key": "a" * 64, "build-deps": list(build), "runtime-deps": list(run)}
