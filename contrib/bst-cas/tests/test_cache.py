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


def test_grep_cached_sources_real_rg(cas, database, capsys):
    from bst_cas.reapi import Source
    from bst_grep.cli import main as grep
    root = cas.tree(files=[('hello.c', cas.put(b'int needle;\nother line\n'), False)],
                    links=[('escape', '/etc/passwd')])
    source = Source()
    source.files.hash, size = root.split('/')
    source.files.size_bytes = int(size)
    for name in ('elementsources', 'source_protos'):
        directory = cas.directory.parent / name
        directory.mkdir()
        (directory / 'key').write_bytes(source.SerializeToString())
    args = ['--in', 'cached-sources', '--cas-directory', str(cas.directory),
            '--cas-index', str(database)]
    assert grep([*args, '-g', '*.c', 'needle']) == 0
    text = capsys.readouterr()
    assert 'hello.c:1:5:int needle;' in text.out
    assert 'unknown-owner [' in text.out
    assert str(cas.directory.parent) not in text.out
    assert '1/1 source trees' in text.err
    assert grep([*args, '--json', '-F', 'other line']) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]['tree_digest'] == root and rows[0]['line'] == 2
    assert rows[-1]['searched'] == 1 and rows[-1]['complete']
    assert grep([*args, 'absent']) == 1
    capsys.readouterr()
    (cas.directory.parent / 'source_protos/bad').write_bytes(b'\xff')
    assert grep([*args, '--json', 'needle']) == 2
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[-1]['matched_lines'] == 1 and not rows[-1]['complete']


def test_cached_source_search_missing_metadata_and_project_args(cas, database, capsys):
    from bst_grep.cli import main as grep
    args = ['--in', 'cached-sources', '--cas-directory', str(cas.directory),
            '--cas-index', str(database), '--json', 'needle']
    assert grep(args) == 2
    assert 'No local source metadata' in json.loads(capsys.readouterr().out)['message']
    assert grep([*args, 'product.bst']) == 2
    assert 'project arguments' in json.loads(capsys.readouterr().out)['message']


def test_source_grep_displays_all_artifact_owners(cas, database, capsys):
    from bst_cas.reapi import Source
    from bst_grep.cli import main as grep
    root = cas.tree(files=[('hello.c', cas.put(b'int needle;\n'), False)])
    source = Source()
    source.files.hash, size = root.split('/')
    source.files.size_bytes = int(size)
    directory = cas.directory.parent / 'elementsources'
    directory.mkdir()
    (directory / 'key').write_bytes(source.SerializeToString())
    refs = cas.directory.parent / 'artifacts/refs'
    for name in ('project/libfoo/strong', 'project/libfoo/weak', 'other/libbar/key'):
        path = write_ref(refs, name)
        artifact = Artifact.FromString(path.read_bytes())
        artifact.sources.hash = source.files.hash
        artifact.sources.size_bytes = source.files.size_bytes
        path.write_bytes(artifact.SerializeToString())
    args = ['--in', 'cached-sources', '--cas-directory', str(cas.directory),
            '--cas-index', str(database), 'needle']
    assert grep(args) == 0
    text = capsys.readouterr().out
    assert 'other/libbar, project/libfoo::hello.c:1:5:int needle;' in text
    assert str(cas.directory.parent) not in text
    assert grep([*args, '--json']) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]['elements'] == ['other/libbar', 'project/libfoo']
    assert len(rows[0]['owners'][1]['artifact_refs']) == 2
    assert rows[0]['ownership'] == 'artifact-source-digest'


def test_source_owners_does_not_guess_from_files_digest(cas, database):
    from bst_cas.cache import source_owners
    root = cas.tree()
    write_ref(cas.directory.parent / 'artifacts/refs', 'project/libfoo/key', root)
    with Index(cas.directory, database) as index:
        owners, errors = source_owners(index)
    assert owners == {} and errors == []
