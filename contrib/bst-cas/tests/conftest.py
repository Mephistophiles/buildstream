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
import hashlib

import pytest

from bst_cas.reapi import Directory


class CAS:
    def __init__(self, directory):
        self.directory = directory
        (directory / "objects").mkdir(parents=True)

    def put(self, content):
        digest = hashlib.sha256(content).hexdigest()
        path = self.directory / "objects" / digest[:2] / digest[2:]
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        return digest + "/" + str(len(content))

    def path(self, digest):
        hash_ = digest.split("/")[0]
        return self.directory / "objects" / hash_[:2] / hash_[2:]

    def tree(self, files=(), directories=(), links=()):
        tree = Directory()
        for name, digest, executable in files:
            node = tree.files.add(name=name, is_executable=executable)
            node.digest.hash, size = digest.split("/")
            node.digest.size_bytes = int(size)
        for name, digest in directories:
            node = tree.directories.add(name=name)
            node.digest.hash, size = digest.split("/")
            node.digest.size_bytes = int(size)
        for name, target in links:
            tree.symlinks.add(name=name, target=target)
        return self.put(tree.SerializeToString())


@pytest.fixture
def cas(tmp_path):
    return CAS(tmp_path / "cas")


@pytest.fixture
def database(tmp_path):
    return tmp_path / "derived" / "index.sqlite3"
