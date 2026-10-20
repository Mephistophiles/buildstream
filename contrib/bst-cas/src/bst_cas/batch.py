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
"""Bounded, identity-checked artifact batches using the public bst show API."""

from .index import digest_parts

BATCH_SIZE = 64
ARGUMENT_BYTES = 24 * 1024
FIELDS = ("name", "full-key", "artifact-cas-digest")


def batches(names):
    batch, size = [], 0
    for name in sorted(set(names)):
        length = len(name.encode("utf-8")) + 1
        if batch and (len(batch) >= BATCH_SIZE or size + length > ARGUMENT_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(name)
        size += length
    if batch:
        yield batch


def identities(index, project, names):
    """Bisect failed queries only; qualified names remain unambiguous in JSON framing."""
    try:
        index.identity_queries += 1
        records = project.report(names, FIELDS)
        result = {}
        for record in records:
            if not isinstance(record, dict) or any(not isinstance(record.get(field), str) for field in FIELDS):
                raise ValueError("Malformed artifact identity record")
            name = record["name"].strip()
            if name not in names or name in result:
                raise ValueError("Unexpected or duplicate artifact identity; project selection changed")
            result[name] = {field: record[field].strip() for field in FIELDS[1:]}
        return result, {name: "Artifact disappeared while querying identity" for name in names if name not in result}
    except (OSError, ValueError) as error:
        if len(names) == 1:
            return {}, {names[0]: str(error)}
        middle = len(names) // 2
        left, errors = identities(index, project, names[:middle])
        right, other_errors = identities(index, project, names[middle:])
        return {**left, **right}, {**errors, **other_errors}


def load_artifacts(index, project, names, expected_keys=None):
    """Yield (name, manifest, identity, error), with at most one batch retained.

    No result is yielded until the batch's second identity query completes.
    Trees are shared within a batch, and revalidated for each later batch so
    a long-running search cannot mask CAS garbage collection.
    """
    expected_keys = expected_keys or {}
    for names in batches(names):
        before, errors = identities(index, project, names)
        manifests, tree_errors = {}, {}
        for name, identity in before.items():
            try:
                digest = identity["artifact-cas-digest"]
                digest_parts(digest)
                if (
                    name in expected_keys
                    and expected_keys[name] is not None
                    and identity["full-key"] != expected_keys[name]
                ):
                    raise ValueError("Project key changed while indexing; retry")
                if digest in tree_errors:
                    raise ValueError(tree_errors[digest])
                if digest in manifests:
                    index.shared_tree_reuses += 1
                else:
                    try:
                        manifests[digest] = index.tree(digest)
                    except (OSError, ValueError) as error:
                        tree_errors[digest] = str(error)
                        raise
            except (OSError, ValueError) as error:
                errors[name] = str(error)
        after, after_errors = (
            identities(index, project, [name for name in names if name in before]) if before else ({}, {})
        )
        for name in names:
            error = errors.get(name) or after_errors.get(name)
            identity = before.get(name)
            if not error and after.get(name) != identity:
                error = "Artifact identity changed while indexing; retry"
            entries = None if error else manifests[identity["artifact-cas-digest"]]
            yield name, entries, identity, error
