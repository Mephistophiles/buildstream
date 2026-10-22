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
"""Wire-compatible projection of REAPI v2 Directory, Digest and child nodes.

Field numbers follow src/buildstream/_protos/build/bazel/remote/execution/v2/
remote_execution.proto in this repository. Unknown properties are preserved by
protobuf but deliberately not interpreted; no private BuildStream imports.
"""

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory


def directory_class(name="Directory"):
    schema = descriptor_pb2.FileDescriptorProto(
        name="bst_cas_directory.proto", package="bst_cas.reapi", syntax="proto3"
    )
    fields = {
        "Artifact": [("version", 1, "int32", False), ("files", 8, "Digest", False)],
        "Digest": [("hash", 1, "string", False), ("size_bytes", 2, "int64", False)],
        "FileNode": [
            ("name", 1, "string", False),
            ("digest", 2, "Digest", False),
            ("is_executable", 4, "bool", False),
        ],
        "DirectoryNode": [("name", 1, "string", False), ("digest", 2, "Digest", False)],
        "SymlinkNode": [("name", 1, "string", False), ("target", 2, "string", False)],
        "Directory": [
            ("files", 1, "FileNode", True),
            ("directories", 2, "DirectoryNode", True),
            ("symlinks", 3, "SymlinkNode", True),
        ],
    }
    types = {"string": 9, "int64": 3, "bool": 8, "int32": 5}
    for message_name, members in fields.items():
        message = schema.message_type.add(name=message_name)
        for field, number, kind, repeated in members:
            value = message.field.add(name=field, number=number, label=3 if repeated else 1, type=types.get(kind, 11))
            if kind not in types:
                value.type_name = ".bst_cas.reapi." + kind
    pool = descriptor_pool.DescriptorPool()
    pool.Add(schema)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("bst_cas.reapi." + name))


Directory = directory_class()

# Projection of buildstream/v2/artifact.proto; unrelated fields are ignored.
Artifact = directory_class("Artifact")
