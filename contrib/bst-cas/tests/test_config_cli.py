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
from pathlib import Path

import pytest

from bst_cas.config import cas_directory
from bst_cas.cli import main


@pytest.fixture
def configuration(tmp_path, monkeypatch):
    monkeypatch.delenv('BST_CAS_DIRECTORY', raising=False)
    monkeypatch.delenv('BST_CAS_INDEX', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    return tmp_path


def test_discovery_and_precedence(configuration, monkeypatch):
    root = configuration
    assert cas_directory() == str(root / 'cache/buildstream/cas')
    (root / 'buildstream.conf').write_text('cachedir: ${XDG_CACHE_HOME}/custom\n')
    assert cas_directory() == str(root / 'cache/custom/cas')
    (root / 'buildstream2.conf').write_text('cachedir: $XDG_CONFIG_HOME/new\n')
    assert cas_directory() == str(root / 'new/cas')
    monkeypatch.setenv('BST_CAS_DIRECTORY', str(root / 'env'))
    assert cas_directory() == str(root / 'env')
    assert cas_directory(str(root / 'explicit')) == str(root / 'explicit')


@pytest.mark.parametrize('value', ['[]', 'cachedir: []', 'cachedir: relative', 'cachedir: null', '[broken'])
def test_bad_config(configuration, value):
    (configuration / 'buildstream2.conf').write_text(value)
    with pytest.raises(ValueError):
        cas_directory()


def test_empty_xdg_defaults(configuration, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', '')
    (configuration / 'buildstream2.conf').write_text('cachedir: $XDG_CACHE_HOME\n')
    assert cas_directory() == str(Path.home() / '.cache/cas')


@pytest.mark.parametrize('tool, extra', [('blame', []), ('grep', ['--in', 'artifacts']), ('elf', []), ('grep-sources', ['--in', 'cached-sources'])])
def test_wrapper_preserves_arguments_and_exit(configuration, monkeypatch, tool, extra):
    import bst_cas.cli as cli
    monkeypatch.setattr(cli.shutil, 'which', lambda name: '/bin/' + name)
    def call(argv, env):
        assert argv == ['/bin/bst-' + ('grep' if tool == 'grep-sources' else tool), *extra, '--json', 'needle with spaces', 'product.bst', '--deps', 'run']
        assert env['BST_CAS_DIRECTORY'] == str(configuration / 'cache/buildstream/cas')
        assert env['BST_CAS_INDEX'] == str(configuration / 'db')
        return 2
    monkeypatch.setattr(cli.subprocess, 'call', call)
    assert main(['--cas-index', str(configuration / 'db'), tool, '--json', 'needle with spaces', 'product.bst', '--deps', 'run']) == 2


def test_discovered_cache_with_real_index(configuration, capsys):
    (configuration / 'cache/buildstream/cas/objects').mkdir(parents=True)
    assert main(['stats', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['query'] == 'stats'


def test_missing_wrapper_tool(configuration, monkeypatch, capsys):
    monkeypatch.setattr('bst_cas.cli.shutil.which', lambda name: None)
    assert main(['--json', 'blame', '/file', 'product.bst']) == 2
    assert 'missing from PATH' in json.loads(capsys.readouterr().out)['error']


def test_filename_search_text_json_and_no_match(cas, database, capsys):
    from bst_cas import Index
    library = cas.tree(files=[('libanswer.so', cas.put(b'ELF fixture'), False)])
    root = cas.tree(directories=[('lib', library)])
    with Index(cas.directory, database) as index:
        index.tree(root)
    args = ['--cas-directory', str(cas.directory), '--cas-index', str(database)]
    assert main([*args, 'find', 'libanswer.so']) == 0
    text = capsys.readouterr().out
    assert '/lib/libanswer.so' in text and 'tree: ' + root in text
    assert main([*args, 'find', 'libanswer*', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['entries'][0]['path'] == '/lib/libanswer.so'
    assert main([*args, 'find', 'lib/libanswer.so']) == 0
    capsys.readouterr()
    assert main([*args, 'find', 'absent']) == 1
    assert 'bst-cas find NAME TARGET.bst' in capsys.readouterr().out


def test_stats_displays_actual_paths_and_index_scope(cas, database, capsys):
    assert main(['--cas-directory', str(cas.directory), '--cas-index', str(database), 'stats']) == 0
    text = capsys.readouterr().out
    assert str(cas.directory) in text and str(database) in text
    assert 'Indexed trees: 0' in text and 'not all objects' in text


def test_find_live_targets_uses_blame_and_forwards_options(configuration, monkeypatch):
    import bst_cas.cli as cli
    monkeypatch.setattr(cli.shutil, 'which', lambda name: '/bin/' + name)
    def call(argv, env):
        assert argv == ['/bin/bst-blame', '--json', '--glob', '--deps', 'run',
                        '-C', '/project', '--option', 'arch', 'arm64', '--', '*/libc++.so', 'product.bst']
        assert env['BST_CAS_DIRECTORY'].endswith('/cache/buildstream/cas')
        return 1
    monkeypatch.setattr(cli.subprocess, 'call', call)
    assert main(['find', 'libc++.so', 'product.bst', '--json', '--deps', 'run',
                 '-C', '/project', '--option', 'arch', 'arm64']) == 1


def test_empty_index_help_and_text_errors(cas, database, capsys):
    args = ['--cas-directory', str(cas.directory), '--cas-index', str(database)]
    assert main([*args, 'find', 'libc++.so']) == 1
    assert 'previously indexed metadata' in capsys.readouterr().out
    assert main([*args, 'find', '../lib']) == 2
    output = capsys.readouterr()
    assert not output.out and 'bst-cas:' in output.err
