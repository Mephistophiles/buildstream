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
import copy
import json
import pytest
from bst_elf.resolve import Resolver, load_profile


def file(owner, path, needed=(), rpath=None, runpath=None, machine="EM_X86_64"):
    return {
        "id": owner + "::" + path,
        "owner": owner,
        "path": path,
        "kind": "file",
        "elf": {
            "machine": machine,
            "class": 64,
            "little_endian": True,
            "type": "ET_DYN",
            "interpreter": None,
            "needed": list(needed),
            "rpath": rpath,
            "runpath": runpath,
            "nodefaultlib": False,
            "soname": None,
        },
    }


def test_origin_transitive_rpath_and_runpath_noninheritance():
    app = file("app", "/bin/app", ["liba.so"], rpath=["$ORIGIN/../private"])
    lib = file("lib", "/private/liba.so", ["libb.so"])
    leaf = file("leaf", "/private/libb.so")
    resolver = Resolver([app, lib, leaf], load_profile())
    contexts, issues, errors = resolver.audit(["/bin/app"])
    assert not issues and not errors and len(contexts) == 3
    assert contexts[1]["inherited_rpath"] == ["/private"]
    app["elf"]["runpath"] = app["elf"].pop("rpath")
    app["elf"]["rpath"] = None
    contexts, issues, errors = resolver.audit(["/bin/app"])
    assert issues[0]["needed"] == "libb.so" and issues[0]["status"] == "missing" and not errors


def test_virtual_symlinks_architecture_and_overlapping_owners():
    app = file("app", "/bin/app", ["liba.so"])
    lib = file("lib", "/real/liba.so")
    directory = {"id": "alias::/usr/lib", "owner": "alias", "path": "/usr/lib", "kind": "symlink", "target": "../real"}
    resolver = Resolver([app, lib, directory], load_profile())
    contexts, issues, errors = resolver.audit(["/bin/app"])
    assert not issues and not errors
    assert contexts[0]["dependencies"][0]["candidates"][0]["links"] == ["alias::/usr/lib"]
    other = file("other", "/real/liba.so", machine="EM_AARCH64")
    result = Resolver([app, lib, directory, other], load_profile()).audit(["/bin/app"])[1][0]
    assert result["status"] == "ambiguous" and result["rejected"][0]["reason"] == "architecture"
    result = Resolver([app, other, directory], load_profile()).audit(["/bin/app"])[1][0]
    assert result["status"] == "incompatible"


def test_search_precedence_nodefaultlib_and_configured_tokens():
    app = file("app", "/bin/app", ["lib.so"], rpath=["/old"], runpath=["/$LIB"])
    entries = [app, file("old", "/old/lib.so"), file("env", "/env/lib.so"), file("new", "/lib64/lib.so")]
    profile = load_profile(library_path=["/env"])
    profile["lib"] = "lib64"
    result = Resolver(entries, profile).audit(["/bin/app"])[0][0]["dependencies"][0]
    assert result["candidates"][0]["owner"] == "env"
    profile["library_path"] = []
    result = Resolver(entries, profile).audit(["/bin/app"])[0][0]["dependencies"][0]
    assert result["candidates"][0]["owner"] == "new"
    app["elf"].update(rpath=None, runpath=None, nodefaultlib=True)
    assert Resolver([app, file("default", "/lib/lib.so")], profile).audit(["/bin/app"])[1][0]["status"] == "missing"


def test_cycles_relative_needed_errors_and_limits():
    a = file("a", "/lib/a", ["b"])
    b = file("b", "/lib/b", ["a"])
    assert len(Resolver([a, b], load_profile()).audit(["/lib/a"])[0]) == 2
    assert Resolver([a, b], load_profile()).audit(["/lib/a"], max_contexts=1)[2]
    a["elf"]["needed"] = ["relative/library.so"]
    assert Resolver([a], load_profile()).audit(["/lib/a"])[2]
    profile = load_profile()
    profile["cwd"] = "/target"
    a["elf"]["needed"] = ["$UNCONFIGURED/lib"]
    assert Resolver([a], profile).audit(["/lib/a"])[2]
    a["elf"]["needed"] = ["loop"]
    loop = {"id": "link::/lib/loop", "owner": "link", "path": "/lib/loop", "kind": "symlink", "target": "loop"}
    assert Resolver([a, loop], profile).audit(["/lib/a"])[2]
    with pytest.raises(ValueError):
        Resolver([a], profile).audit(["/missing"])


@pytest.mark.parametrize(
    "value",
    [{"format_version": 2}, {"unknown": True}, {"cwd": "relative"}, {"default_dirs": "/lib"}, {"inherit_rpath": 1}],
)
def test_invalid_profile(tmp_path, value):
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load_profile(p)


def test_root_overlap_hardlink_and_soname_are_not_guessed():
    a = file("a", "/bin/app")
    b = file("b", "/bin/app")
    assert Resolver([a, b], load_profile()).audit(["/bin/app"])[1][0]["reason"] == "ambiguous-root"
    alias = {"id": "a::/bin/alias", "owner": "a", "path": "/bin/alias", "kind": "hardlink", "target": "bin/app"}
    assert Resolver([a, alias], load_profile()).audit(["/bin/alias"])[0][0]["file"] == "a::/bin/app"
    a["elf"]["needed"] = ["lib.so"]
    provider = file("p", "/unsearched/renamed")
    provider["elf"]["soname"] = "lib.so"
    assert Resolver([a, provider], load_profile()).audit(["/bin/app"])[1][0]["status"] == "missing"
