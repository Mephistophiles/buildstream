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
"""SQLite inventories keyed by immutable REAPI tree digests, never by owner names."""

from contextlib import contextmanager
import fnmatch
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from google.protobuf.message import DecodeError

from .reapi import Directory

MAX_DIRECTORY = 16 * 1024 * 1024
MAX_ENTRIES = 1_000_000
MAX_DEPTH = 256


def digest_parts(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}/(?:0|[1-9][0-9]*)", value):
        raise ValueError("Expected SHA-256 CAS digest HASH/SIZE, got: " + repr(value))
    digest, size = value.split("/")
    if int(size) > 2**63 - 1:
        raise ValueError("CAS digest size exceeds int64")
    return digest, int(size)


def encode(value):
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return payload, hashlib.sha256(payload.encode()).hexdigest()


def decode(payload, checksum):
    if hashlib.sha256(payload.encode()).hexdigest() != checksum:
        raise ValueError("CAS index record is corrupt; clear the derived index and retry")
    return json.loads(payload)


def database_errors(method):
    @functools.wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except sqlite3.Error as error:
            raise ValueError("CAS index database: " + str(error)) from error

    return wrapped


class Index:
    """Read the local BuildStream objects/HH/HASH layout; store only derived data.

    The directory is explicit: do not guess a project's configured cachedir.
    Remote storage-service caches must first be made available locally.
    """

    @database_errors
    def __init__(self, cas_directory, database=None):
        if not cas_directory:
            raise ValueError("Set --cas-directory or BST_CAS_DIRECTORY to the local BuildStream cas directory")
        self.cas_directory = Path(cas_directory).expanduser().resolve()
        cache_home = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        self.database = (
            Path(database or os.environ.get("BST_CAS_INDEX") or cache_home / "bst-cas" / "index.sqlite3")
            .expanduser()
            .resolve()
        )
        if self.database.is_relative_to(self.cas_directory):
            raise ValueError("The derived index must be outside the read-only CAS directory")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        self.objects_fd = None
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported CAS index schema; use a new --cas-index path")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS trees (
                    digest TEXT PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL,
                    accessed REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS analyses (
                    digest TEXT NOT NULL, namespace TEXT NOT NULL,
                    payload TEXT NOT NULL, checksum TEXT NOT NULL, accessed REAL NOT NULL,
                    PRIMARY KEY (digest, namespace));
                CREATE TABLE IF NOT EXISTS verified (
                    location TEXT NOT NULL, digest TEXT NOT NULL, signature TEXT NOT NULL,
                    PRIMARY KEY (location, digest));
                PRAGMA user_version=1;
            """)
            self.objects_fd = os.open(self.cas_directory / "objects", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except BaseException:
            self.close()
            raise
        self.hits = self.misses = self.analysis_hits = self.analysis_misses = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.objects_fd is not None:
            os.close(self.objects_fd)
            self.objects_fd = None
        self.db.close()

    @contextmanager
    def _open(self, digest):
        hash_, size = digest_parts(digest)
        try:
            shard = os.open(hash_[:2], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.objects_fd)
            try:
                fd = os.open(hash_[2:], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=shard)
            finally:
                os.close(shard)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != size:
                    raise ValueError("Invalid local CAS object type or size: " + digest)
                yield stream
        except OSError as error:
            raise ValueError(
                "Local CAS object unavailable (possibly garbage-collected): " + digest + ": " + str(error)
            ) from error

    @staticmethod
    def _signature(stream):
        info = os.fstat(stream.fileno())
        return repr((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))

    @contextmanager
    def blob(self, digest):
        """Verified seekable read-only content. No links, fetching or execution."""
        with self._database_context(), self._open(digest) as stream:
            before = self._signature(stream)
            row = self.db.execute(
                "SELECT signature FROM verified WHERE location=? AND digest=?", (str(self.cas_directory), digest)
            ).fetchone()
            if row is None or row[0] != before:
                hash_ = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    hash_.update(chunk)
                if hash_.hexdigest() != digest_parts(digest)[0] or before != self._signature(stream):
                    raise ValueError("CAS object checksum mismatch or concurrent mutation: " + digest)
                self.db.execute(
                    "INSERT OR REPLACE INTO verified VALUES (?, ?, ?)", (str(self.cas_directory), digest, before)
                )
                stream.seek(0)
            yield stream
            if before != self._signature(stream):
                raise ValueError("CAS object changed while reading: " + digest)

    @contextmanager
    def _database_context(self):
        try:
            yield
        except sqlite3.Error as error:
            raise ValueError("CAS index database: " + str(error)) from error

    @database_errors
    def tree(self, digest):
        digest_parts(digest)
        row = self.db.execute("SELECT payload, checksum FROM trees WHERE digest=?", (digest,)).fetchone()
        if row:
            inventory = decode(*row)
            self.hits += 1
        else:
            inventory = self._walk(digest)
            self.misses += 1
        # Derived metadata must never make a GC'd local artifact look present.
        # Directories are checksum-verified, file presence is checked without
        # reading file bodies. File content is verified on blob()/analyze().
        for directory in inventory["directories"]:
            with self.blob(directory):
                pass
        for entry in inventory["entries"]:
            if entry["kind"] == "file":
                with self._open(entry["digest"]):
                    pass
        if not row:
            payload, checksum = encode(inventory)
            self.db.execute(
                "INSERT OR REPLACE INTO trees VALUES (?, ?, ?, ?)", (digest, payload, checksum, time.time())
            )
        else:
            self.db.execute("UPDATE trees SET accessed=? WHERE digest=?", (time.time(), digest))
        return inventory["entries"]

    def _walk(self, root):
        entries, directories = [], set()
        pending = [("", root, 0)]
        while pending:
            prefix, digest, depth = pending.pop()
            if depth > MAX_DEPTH or len(entries) > MAX_ENTRIES:
                raise ValueError("CAS tree exceeds depth/entry limit")
            if digest_parts(digest)[1] > MAX_DIRECTORY:
                raise ValueError("CAS Directory exceeds 16 MiB")
            with self.blob(digest) as stream:
                try:
                    directory = Directory.FromString(stream.read())
                except DecodeError as error:
                    raise ValueError("Malformed REAPI Directory: " + digest) from error
            directories.add(digest)
            seen = set()
            for kind, nodes in (
                ("file", directory.files),
                ("directory", directory.directories),
                ("symlink", directory.symlinks),
            ):
                for node in nodes:
                    name = node.name
                    if (
                        not name
                        or name in (".", "..")
                        or "/" in name
                        or any(c in name for c in ("\x00", "\n", "\r"))
                        or name in seen
                    ):
                        raise ValueError("Invalid or duplicate CAS Directory entry: " + repr(name))
                    seen.add(name)
                    path = prefix + "/" + name
                    entry = {"path": path, "kind": kind, "size": 0, "executable": False, "link_target": None}
                    if kind == "symlink":
                        if "\x00" in node.target:
                            raise ValueError("NUL in CAS symlink target")
                        entry.update(link_target=node.target, size=len(node.target))
                    else:
                        child_digest = node.digest.hash + "/" + str(node.digest.size_bytes)
                        digest_parts(child_digest)
                        entry["digest"] = child_digest
                        if kind == "file":
                            entry.update(size=node.digest.size_bytes, executable=node.is_executable)
                        else:
                            pending.append((path, child_digest, depth + 1))
                    entries.append(entry)
                    if len(entries) > MAX_ENTRIES:
                        raise ValueError("CAS tree exceeds entry limit")
        return {"entries": sorted(entries, key=lambda e: e["path"]), "directories": sorted(directories)}

    @database_errors
    def analyze(self, digest, namespace, analyzer):
        """Cache a JSON-serializable analysis; caller versions its parser/options."""
        with self.blob(digest) as stream:
            row = self.db.execute(
                "SELECT payload, checksum FROM analyses WHERE digest=? AND namespace=?", (digest, namespace)
            ).fetchone()
            if row:
                result = decode(*row)
                self.analysis_hits += 1
                self.db.execute(
                    "UPDATE analyses SET accessed=? WHERE digest=? AND namespace=?", (time.time(), digest, namespace)
                )
                return result
            result = analyzer(stream)
            payload, checksum = encode(result)
        self.db.execute(
            "INSERT OR REPLACE INTO analyses VALUES (?, ?, ?, ?, ?)",
            (digest, namespace, payload, checksum, time.time()),
        )
        self.analysis_misses += 1
        return result

    @database_errors
    def find(self, pattern, digest=None):
        """Search indexed trees only. This does not claim a live owner or cache state."""
        if digest:
            digest_parts(digest)
        rows = self.db.execute(
            "SELECT digest, payload, checksum FROM trees" + (" WHERE digest=?" if digest else ""),
            (digest,) if digest else (),
        )
        return [
            {"tree_digest": tree, **entry}
            for tree, payload, checksum in rows
            for entry in decode(payload, checksum)["entries"]
            if fnmatch.fnmatchcase(entry["path"], pattern)
        ]

    @database_errors
    def stats(self):
        return {
            "trees": self.db.execute("SELECT count(*) FROM trees").fetchone()[0],
            "analyses": self.db.execute("SELECT count(*) FROM analyses").fetchone()[0],
            "hits": self.hits,
            "misses": self.misses,
            "analysis_hits": self.analysis_hits,
            "analysis_misses": self.analysis_misses,
        }

    @database_errors
    def prune(self, older_than_days):
        cutoff = time.time() - older_than_days * 86400
        self.db.execute("BEGIN IMMEDIATE")
        try:
            deleted = {
                table: self.db.execute(f"DELETE FROM {table} WHERE accessed < ?", (cutoff,)).rowcount
                for table in ("trees", "analyses")
            }
            self.db.execute("DELETE FROM verified")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return deleted


def load_artifact(index, project, name, expected_key=None):
    """Bind a reusable tree to a current owner using public bst show, twice."""

    def identity():
        records = project.report([name], ("full-key", "artifact-cas-digest"))
        if len(records) != 1:
            raise ValueError("Expected one artifact identity")
        return {key: value.strip() for key, value in records[0].items()}

    before = identity()
    digest_parts(before["artifact-cas-digest"])
    if expected_key is not None and before["full-key"] != expected_key:
        raise ValueError("Project key changed while indexing; retry")
    entries = index.tree(before["artifact-cas-digest"])
    if identity() != before:
        raise ValueError("Artifact identity changed while indexing; retry")
    return entries, before
