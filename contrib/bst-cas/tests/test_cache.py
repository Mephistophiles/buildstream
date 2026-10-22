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
import json

from bst_cas import Index
from bst_cas.cache import index_cache
from bst_cas.cli import main
from bst_cas.reapi import Artifact


def write_ref(refs, name, digest=None, version=2):
    artifact = Artifact(version=version)
    if digest is not None:
        artifact.files.hash, size = digest.split('/')
        artifact.files.size_bytes = int(size)
    path = refs / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(artifact.SerializeToString())
    return path


def test_cache_indexes_deduplicates_and_searches(cas, database, capsys):
    refs = cas.directory.parent / 'artifacts/refs'
    root = cas.tree(files=[('hello.txt', cas.put(b'hello'), False)])
    write_ref(refs, 'project/element/strong', root)
    write_ref(refs, 'project/element/weak', root)
    write_ref(refs, 'project/failed/key')
    args = ['--cas-directory', str(cas.directory), '--cas-index', str(database)]
    assert main([*args, 'index-cache', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['complete'] and report['refs'] == 3
    assert report['indexed_trees'] == 1 and report['reused_refs'] == 1 and report['without_files'] == 1
    assert main([*args, 'find', 'hello.txt']) == 0
    assert '/hello.txt' in capsys.readouterr().out
    assert main([*args, 'index-cache', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['stats']['hits'] == 1


def test_cache_keeps_good_results_on_bad_refs_and_missing_trees(cas, database, tmp_path):
    refs = tmp_path / 'refs'
    good = cas.tree(files=[('ok', cas.put(b'ok'), False)])
    missing = cas.tree(links=[('gone', 'target')])
    cas.path(missing).unlink()
    write_ref(refs, 'p/e/good', good)
    write_ref(refs, 'p/e/missing', missing)
    write_ref(refs, 'p/e/future', good, version=99)
    bad = refs / 'p/e/bad'
    bad.write_bytes(b'\xff')
    (refs / 'p/e/link').symlink_to(refs / 'p/e/good')
    with Index(cas.directory, database) as index:
        result = index_cache(index, refs)
        assert not result['complete'] and len(result['errors']) == 4
        assert result['indexed_trees'] == 1
        assert index.find('/ok')


def test_cache_empty_and_missing_refs_are_distinguished(cas, database, tmp_path, capsys):
    refs = tmp_path / 'empty'
    args = ['--cas-directory', str(cas.directory), '--cas-index', str(database),
            'index-cache', '--refs-directory', str(refs)]
    assert main(args) == 2
    assert 'bst-cas:' in capsys.readouterr().err
    refs.mkdir()
    assert main(args) == 0
    assert 'Read 0 refs' in capsys.readouterr().out
    write_ref(refs, 'bad', version=9)
    assert main(args) == 2
    assert 'INCOMPLETE' in capsys.readouterr().out
