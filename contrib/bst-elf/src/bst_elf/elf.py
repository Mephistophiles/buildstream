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
"""Read ELF metadata with pyelftools. No target program or loader is executed."""

from elftools.elf.elffile import ELFFile
from elftools.elf.dynamic import DynamicSegment
from elftools.common.exceptions import ELFError


def read_elf(stream):
    try:
        stream.seek(0)
        elf = ELFFile(stream)
        result = {
            "class": elf.elfclass,
            "little_endian": elf.little_endian,
            "machine": elf.header.e_machine,
            "type": elf.header.e_type,
            "osabi": elf.header.e_ident.EI_OSABI,
            "interpreter": None,
            "needed": [],
            "soname": None,
            "rpath": None,
            "runpath": None,
            "nodefaultlib": False,
        }
        for segment in elf.iter_segments():
            if segment.header.p_type == "PT_INTERP":
                result["interpreter"] = segment.get_interp_name()
            if isinstance(segment, DynamicSegment):
                for tag in segment.iter_tags():
                    kind = tag.entry.d_tag
                    if kind == "DT_NEEDED":
                        result["needed"].append(tag.needed)
                    elif kind == "DT_SONAME":
                        result["soname"] = tag.soname
                    elif kind == "DT_RPATH":
                        result["rpath"] = tag.rpath.split(":")
                    elif kind == "DT_RUNPATH":
                        result["runpath"] = tag.runpath.split(":")
                    elif kind == "DT_FLAGS_1":
                        result["nodefaultlib"] = bool(tag.entry.d_val & 0x800)
        return result
    except (ELFError, OSError, ValueError, TypeError, KeyError, UnicodeError, AssertionError) as error:
        raise ValueError("Invalid or unsupported ELF: " + str(error)) from error
