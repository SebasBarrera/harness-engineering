"""Workspace snapshots for large repositories (``workspace.snapshot``, ``workspace.baseline``,
``workspace.snapshotCache``; since 1.1).

In 1.0.0 every snapshot walks the whole workspace, hashes every file, reads the text of every
file and stores the baseline as one JSON artifact with the text of every file: on a repository
of 10,000 files (117 MB) one run took 21.5 s, peaked at 1.29 GB and stored a 107 MB blob that
also held ``.env``. The settings, each optional:

* ``snapshot: git`` lists the files through Git: tracked files plus untracked files that
  ``.gitignore`` does not exclude, minus the directories the ChangeSet always excludes and
  symbolic links. Ignored files are never read, hashed or stored. A workspace that is not a Git
  repository is walked as before.
* ``baseline: manifest`` stores a snapshot as a manifest of digests. The text is kept only for
  files whose content Git cannot give back later (untracked or modified at the time of the
  snapshot); every other file is read from the recorded ``HEAD`` when, and only when, it enters
  a diff. A file whose blob does not match its recorded digest (a Git filter changed it) diffs
  as binary.
* ``snapshotCache: true`` keeps, in ``.harness/cache/snapshot-cache.json``, the digest of each
  file with its size, modification time, change time and inode; a file whose four values did
  not change is not hashed again. The change time cannot be set by a process (``utime`` updates
  it), so restoring a file's modification time does not hide an edit. Entries younger than two
  seconds are not cached. Windows reports the creation time instead of the change time, so the
  cache is not used there.

Without any of them a snapshot is taken, stored and diffed exactly as in 1.0.0."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - git ls-files with a fixed argv, no shell
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from governed_harness.configuration.models import WorkspaceConfig
from governed_harness.evidence.artifact_store import ArtifactRef, LocalArtifactStore
from governed_harness.evidence.hashing import sha256_bytes, sha256_file, sha256_json
from governed_harness.runtime.workspace import (
    DEFAULT_EXCLUDES,
    FileState,
    TextResolver,
    WorkspaceDiff,
    WorkspaceSnapshot,
    WorkspaceSnapshotter,
    decode_text,
)

MANIFEST_FORMAT = "manifest/1"
CACHE_FILE = Path("cache") / "snapshot-cache.json"
SNAPSHOT_CACHE_SEAL = "snapshot-cache-seal"
"""State flag holding the digest of the cache file."""
RACY_SECONDS = 2.0
_GIT_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class SnapshotSettings:
    git_listing: bool = False
    manifest: bool = False
    cache: bool = False

    @classmethod
    def from_config(cls, workspace: WorkspaceConfig) -> SnapshotSettings:
        return cls(
            git_listing=workspace.snapshot == "git",
            manifest=workspace.baseline == "manifest",
            cache=bool(workspace.snapshot_cache),
        )

    @property
    def scaled(self) -> bool:
        """Whether any 1.1 setting is on (otherwise everything works as in 1.0.0)."""
        return self.git_listing or self.manifest or self.cache


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(  # nosec B603 B607 - git from PATH, argv built here, no shell
            ["git", *args],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def git_head(root: Path) -> str | None:
    result = _git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if result is None or result.returncode != 0:
        return None
    return result.stdout.decode().strip() or None


def git_listing(root: Path) -> list[str] | None:
    """Tracked files and untracked files Git does not ignore; ``None`` outside a repository."""
    result = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if result is None or result.returncode != 0:
        return None
    names = result.stdout.decode("utf-8", "surrogateescape").split("\x00")
    return sorted({name for name in names if name})


def git_ignored(root: Path) -> list[str]:
    """Untracked files ``.gitignore`` excludes (for the out-of-ChangeSet guard)."""
    result = _git(
        root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"
    )
    if result is None or result.returncode != 0:
        return []
    return sorted(
        name for name in result.stdout.decode("utf-8", "surrogateescape").split("\x00") if name
    )


def git_dirty(root: Path) -> set[str] | None:
    """Paths whose content Git cannot give back from ``HEAD`` (modified, added, untracked)."""
    result = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    if result is None or result.returncode != 0:
        return None
    dirty: set[str] = set()
    for entry in result.stdout.decode("utf-8", "surrogateescape").split("\x00"):
        if len(entry) > 3:
            dirty.add(entry[3:])
    return dirty


def git_blob(root: Path, revision: str, path: str) -> bytes | None:
    result = _git(root, "cat-file", "blob", f"{revision}:{path}")
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def _excluded(relative: str, excludes: set[str]) -> bool:
    return any(part in excludes for part in PurePosixPath(relative).parts[:-1])


Seal = tuple[Callable[[], str | None], Callable[[str], None]]
"""Read and write the digest of the cache file kept outside it (in the state database)."""


class SnapshotCache:
    """The digest cache. Its file is sealed: the digest of its content is kept in the state
    database after every write, and a file that does not match the seal (edited by anything
    but the harness) is ignored, so every file is hashed again."""

    def __init__(self, path: Path, seal: Seal | None = None) -> None:
        self.path = path
        self.seal = seal
        self.entries: dict[str, list[Any]] = {}
        self.hits = 0
        self.misses = 0
        try:
            data = path.read_bytes()
            if seal is not None and seal[0]() != sha256_bytes(data):
                return
            value = json.loads(data)
            if value.get("version") == 1 and isinstance(value.get("entries"), dict):
                self.entries = value["entries"]
        except (OSError, ValueError, AttributeError):
            self.entries = {}

    @staticmethod
    def key(stat: os.stat_result) -> list[int]:
        return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]

    def digest(self, relative: str, stat: os.stat_result) -> str | None:
        entry = self.entries.get(relative)
        if entry is not None and entry[:4] == self.key(stat):
            self.hits += 1
            return str(entry[4])
        self.misses += 1
        return None

    def save(self, current: dict[str, list[Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = json.dumps({"version": 1, "entries": current}, separators=(",", ":")).encode()
        handle, name = tempfile.mkstemp(prefix=".snapshot-cache.", dir=self.path.parent)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        if self.seal is not None:
            self.seal[1](sha256_bytes(data))


class ScaledSnapshotter(WorkspaceSnapshotter):
    """A snapshotter that lists through Git, caches digests and reads text lazily."""

    def __init__(
        self,
        root: Path,
        settings: SnapshotSettings,
        cache_path: Path | None = None,
        seal: Seal | None = None,
    ) -> None:
        super().__init__(root)
        self.settings = settings
        self.cache_path = cache_path
        self.seal = seal

    def _paths(self) -> Iterator[str]:
        listed = git_listing(self.root) if self.settings.git_listing else None
        if listed is None:
            for directory, dirnames, filenames in os.walk(self.root, followlinks=False):
                dirnames[:] = [
                    name
                    for name in sorted(dirnames)
                    if name not in self.excludes and not (Path(directory) / name).is_symlink()
                ]
                for filename in sorted(filenames):
                    yield (Path(directory) / filename).relative_to(self.root).as_posix()
            return
        for relative in listed:
            if not _excluded(relative, self.excludes):
                yield relative

    def snapshot(self, *, read_text: bool = False) -> WorkspaceSnapshot:
        # Windows reports the creation time as st_ctime, not the change time: a restored
        # modification time would hide an edit, so every file is hashed there.
        cache = (
            SnapshotCache(self.cache_path, self.seal)
            if self.settings.cache and self.cache_path and os.name != "nt"
            else None
        )
        fresh: dict[str, list[Any]] = {}
        states: dict[str, FileState] = {}
        now_ns = time.time_ns()
        for relative in self._paths():
            path = self.root / relative
            try:
                stat = path.lstat()
            except OSError:
                continue
            if not os.path.isfile(path) or path.is_symlink():
                continue
            try:
                digest = cache.digest(relative, stat) if cache else None
                if digest is None:
                    digest = sha256_file(path)
                text = self._read_text(path, stat.st_size) if read_text else None
            except OSError:
                continue
            if cache is not None and now_ns - stat.st_mtime_ns > RACY_SECONDS * 1e9:
                fresh[relative] = [*SnapshotCache.key(stat), digest]
            states[relative] = FileState(relative, digest, stat.st_size, text, read_text)
        if cache is not None:
            cache.save(fresh)
        digest = sha256_json({path: state.digest for path, state in sorted(states.items())})
        return WorkspaceSnapshot(files=states, digest=digest)


@dataclass
class StoredSnapshot:
    snapshot: WorkspaceSnapshot
    git_head: str | None = None
    inline: dict[str, str | None] | None = None
    """Manifest format: path -> text kept in the manifest (files Git cannot give back)."""


class SnapshotStore:
    """Take, store and load workspace snapshots under the workspace settings."""

    def __init__(
        self,
        workspace: Path,
        artifacts: LocalArtifactStore,
        settings: SnapshotSettings,
        harness_dir: Path | None = None,
        seal: Seal | None = None,
    ) -> None:
        self.workspace = workspace
        self.artifacts = artifacts
        self.settings = settings
        self.cache_path = (harness_dir or workspace / ".harness") / CACHE_FILE
        self.seal = seal

    def snapshotter(self) -> WorkspaceSnapshotter:
        if not self.settings.scaled:
            return WorkspaceSnapshotter(self.workspace)
        return ScaledSnapshotter(self.workspace, self.settings, self.cache_path, self.seal)

    def take(self, *, for_storage: bool = False) -> WorkspaceSnapshot:
        """A snapshot of the workspace. Under the 1.0.0 settings it reads every text; a scaled
        snapshot reads text only when it is stored in the 1.0.0 format."""
        snapshotter = self.snapshotter()
        if isinstance(snapshotter, ScaledSnapshotter):
            return snapshotter.snapshot(read_text=for_storage and not self.settings.manifest)
        return snapshotter.snapshot()

    def store(self, snapshot: WorkspaceSnapshot, metadata: dict[str, Any]) -> ArtifactRef:
        if not self.settings.manifest:
            return self.artifacts.put_json(text_document(snapshot), metadata=metadata)
        head = git_head(self.workspace)
        dirty = git_dirty(self.workspace) if head else None
        files: dict[str, Any] = {}
        for path, state in snapshot.files.items():
            entry: dict[str, Any] = {
                "path": state.path,
                "digest": state.digest,
                "sizeBytes": state.size_bytes,
            }
            if head is None or dirty is None or path in dirty:
                text = state.text
                if not state.text_loaded:
                    target = self.workspace / path
                    try:
                        data = target.read_bytes()
                    except OSError:
                        data = None
                    text = (
                        decode_text(data)
                        if data is not None and sha256_bytes(data) == state.digest
                        else None
                    )
                entry["text"] = text
            files[path] = entry
        document = {
            "format": MANIFEST_FORMAT,
            "digest": snapshot.digest,
            "gitHead": head,
            "files": files,
        }
        return self.artifacts.put_json(document, metadata=metadata)

    def load(self, uri: str) -> StoredSnapshot:
        value = json.loads(self.artifacts.get(uri))
        if value.get("format") != MANIFEST_FORMAT:
            return StoredSnapshot(from_text_document(value))
        inline: dict[str, str | None] = {}
        states: dict[str, FileState] = {}
        for path, item in value["files"].items():
            if "text" in item:
                inline[path] = item["text"]
                states[path] = FileState(path, item["digest"], item["sizeBytes"], item["text"])
            else:
                states[path] = FileState(path, item["digest"], item["sizeBytes"], None, False)
        return StoredSnapshot(
            WorkspaceSnapshot(files=states, digest=value["digest"]), value.get("gitHead"), inline
        )

    def content(self, stored: StoredSnapshot, path: str) -> bytes | None:
        """The bytes of a file as the snapshot recorded it, when they can be given back."""
        state = stored.snapshot.files.get(path)
        if state is None:
            return None
        if state.text_loaded:
            return state.text.encode("utf-8") if state.text is not None else None
        if stored.git_head is None:
            return None
        data = git_blob(self.workspace, stored.git_head, path)
        return data if data is not None and sha256_bytes(data) == state.digest else None

    def resolver(self, stored: StoredSnapshot) -> TextResolver:
        def resolve(side: str, state: FileState) -> str | None:
            if side == "before":
                data = self.content(stored, state.path)
                return decode_text(data) if data is not None else None
            target = self.workspace / state.path
            try:
                data = target.read_bytes()
            except OSError:
                return None
            return decode_text(data) if sha256_bytes(data) == state.digest else None

        return resolve

    def diff(self, stored: StoredSnapshot, current: WorkspaceSnapshot) -> WorkspaceDiff:
        return self.snapshotter().diff(stored.snapshot, current, resolve_text=self.resolver(stored))


def text_document(snapshot: WorkspaceSnapshot) -> dict[str, Any]:
    """The 1.0.0 stored form: every file with its text."""
    return {
        "digest": snapshot.digest,
        "files": {
            path: {
                "path": state.path,
                "digest": state.digest,
                "sizeBytes": state.size_bytes,
                "text": state.text,
            }
            for path, state in snapshot.files.items()
        },
    }


def from_text_document(value: dict[str, Any]) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        files={
            path: FileState(
                path=item["path"],
                digest=item["digest"],
                size_bytes=item["sizeBytes"],
                text=item.get("text"),
            )
            for path, item in value["files"].items()
        },
        digest=value["digest"],
    )


def ignored_outside_excludes(root: Path) -> list[str]:
    """Ignored files outside the directories the ChangeSet always excludes: under ``snapshot:
    git`` they leave the ChangeSet, so the out-of-ChangeSet guard watches them."""
    found: list[str] = []
    for name in git_ignored(root):
        if name.endswith("/"):
            directory = name.rstrip("/")
            if _excluded(f"{directory}/x", set(DEFAULT_EXCLUDES)):
                continue
            base = root / directory
            if base.is_symlink() or not base.is_dir():
                continue
            for current, dirnames, filenames in os.walk(base, followlinks=False):
                dirnames[:] = [item for item in dirnames if item not in DEFAULT_EXCLUDES]
                for filename in filenames:
                    found.append((Path(current) / filename).relative_to(root).as_posix())
        elif not _excluded(name, set(DEFAULT_EXCLUDES)):
            found.append(name)
    return sorted(found)
