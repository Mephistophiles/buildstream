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
"""Artifact-root paths never refer to the host filesystem."""

from pathlib import PurePosixPath


def normalize_path(value):
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("Artifact paths must be nonempty and contain no NUL or line breaks")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ValueError("Artifact paths must not contain '..'")
    return "/" + str(PurePosixPath("/", str(path).lstrip("/"))).lstrip("/")
