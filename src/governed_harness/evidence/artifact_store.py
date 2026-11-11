from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hashing import sha256_bytes
from .redaction import PRIVATE_KEY, PRIVATE_KEY_MARK, SecretRedactor

# Windows only: how many times the move into place is tried, and the first pause (doubled each time).
_WINDOWS_REPLACE_ATTEMPTS = 8
_WINDOWS_REPLACE_BACKOFF_SECONDS = 0.01


@dataclass(frozen=True)
class ArtifactRef:
    uri: str
    digest: str
    size_bytes: int
    media_type: str = "application/octet-stream"
    redacted: bool = False
    metadata: dict[str, Any] | None = None


class LocalArtifactStore:
    """Content-addressed, atomic local store with digest verification."""

    def __init__(self, root: Path, redactor: SecretRedactor | None = None) -> None:
        self.root = root.resolve(strict=False)
        self.blob_root = self.root / "blobs" / "sha256"
        self.meta_root = self.root / "metadata"
        self.blob_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.meta_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.redactor = redactor or SecretRedactor()

    def put(
        self,
        data: bytes,
        *,
        media_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
        redact: bool = True,
        secret_literals: tuple[bytes, ...] = (),
    ) -> ArtifactRef:
        redaction = self.redactor.redact(data, secret_literals) if redact else None
        stored = redaction.data if redaction else data
        rule_ids = redaction.rule_ids if redaction and redaction.redacted else ()
        return self._store(stored, media_type, metadata, rule_ids)

    def _store(
        self,
        stored: bytes,
        media_type: str,
        metadata: dict[str, Any] | None,
        rule_ids: tuple[str, ...],
    ) -> ArtifactRef:
        digest = sha256_bytes(stored)
        hex_digest = digest.split(":", 1)[1]
        path = self._blob_path(hex_digest)
        self._atomic_write(path, stored)
        meta = dict(metadata or {})
        if rule_ids:
            meta["redactionRuleIds"] = list(rule_ids)
        redacted = bool(rule_ids)
        descriptor = {
            "digest": digest,
            "sizeBytes": len(stored),
            "mediaType": media_type,
            "redacted": redacted,
            "metadata": meta,
        }
        self._atomic_write(
            self.meta_root / f"{hex_digest}.json",
            json.dumps(descriptor, sort_keys=True, indent=2).encode("utf-8"),
        )
        return ArtifactRef(
            uri=f"artifact://sha256/{hex_digest}",
            digest=digest,
            size_bytes=len(stored),
            media_type=media_type,
            redacted=redacted,
            metadata=meta,
        )

    def put_json(self, value: Any, *, metadata: dict[str, Any] | None = None) -> ArtifactRef:
        """Store ``value`` as JSON, redacted as text like any artifact and on its decoded
        strings. The serialized text escapes quotes, so a rule run over it misses a secret whose
        value is quoted inside a JSON string (``password = \\"...\\"``, #178), and it can cut an
        escape or span two strings and leave a document that no longer parses (#152). When any
        decoded string redacts, or the text redaction breaks the document, the strings are
        redacted one by one instead; a document whose strings have nothing to redact is stored
        exactly as before, with the same bytes and digest.

        A redacted JSON artifact is evidence, not data: a caller that reads one back to act on
        it (a pinned task, a workspace snapshot) checks the ``redacted`` flag of its descriptor
        and never applies a redacted value."""
        data = _json_bytes(value)
        redaction = self.redactor.redact(data)
        redacted, rule_ids = self.redactor.redact_json(value)
        if not rule_ids and (not redaction.redacted or _parses(redaction.data)):
            return self._store(redaction.data, "application/json", metadata, redaction.rule_ids)
        data = _json_bytes(redacted)
        # A private key whose lines are separate strings is caught only on the whole text: the
        # text redaction is kept when it parses, otherwise nothing of the document is kept.
        spanning, count = PRIVATE_KEY.subn(PRIVATE_KEY_MARK, data)
        if count:
            data = (
                spanning
                if _parses(spanning)
                else _json_bytes({"redactedDocument": PRIVATE_KEY_MARK.decode()})
            )
            if "private_key" not in rule_ids:
                rule_ids = (*rule_ids, "private_key")
        return self._store(data, "application/json", metadata, rule_ids)

    def get(self, reference: ArtifactRef | str) -> bytes:
        digest = (
            reference.digest
            if isinstance(reference, ArtifactRef)
            else self._digest_from_uri(reference)
        )
        hex_digest = digest.split(":", 1)[1]
        path = self._blob_path(hex_digest)
        data = path.read_bytes()
        if sha256_bytes(data) != digest:
            raise ValueError("artifact digest mismatch")
        return data

    def verify(self, reference: ArtifactRef | str) -> bool:
        try:
            self.get(reference)
            return True
        except (OSError, ValueError):
            return False

    def delete(self, reference: ArtifactRef | str) -> bool:
        """Remove a blob and its descriptor (retention); ``False`` when it was not stored."""
        digest = (
            reference.digest
            if isinstance(reference, ArtifactRef)
            else self._digest_from_uri(reference)
        )
        hex_digest = digest.split(":", 1)[1]
        path = self._blob_path(hex_digest)
        existed = path.exists()
        path.unlink(missing_ok=True)
        (self.meta_root / f"{hex_digest}.json").unlink(missing_ok=True)
        return existed

    def describe(self, uri: str) -> ArtifactRef:
        digest = self._digest_from_uri(uri)
        hex_digest = digest.split(":", 1)[1]
        descriptor = json.loads((self.meta_root / f"{hex_digest}.json").read_text(encoding="utf-8"))
        return ArtifactRef(
            uri=uri,
            digest=digest,
            size_bytes=descriptor["sizeBytes"],
            media_type=descriptor["mediaType"],
            redacted=descriptor.get("redacted", False),
            metadata=descriptor.get("metadata", {}),
        )

    def list(self) -> list[ArtifactRef]:
        refs: list[ArtifactRef] = []
        for path in sorted(self.meta_root.glob("*.json")):
            hex_digest = path.stem
            refs.append(self.describe(f"artifact://sha256/{hex_digest}"))
        return refs

    def path_for(self, reference: ArtifactRef | str) -> Path:
        """Where the content of an artifact is stored (read-only use: an agent may read a
        person's attachment from there)."""
        uri = reference.uri if isinstance(reference, ArtifactRef) else reference
        return self._blob_path(self._digest_from_uri(uri).split(":", 1)[1])

    def _blob_path(self, hex_digest: str) -> Path:
        if len(hex_digest) != 64 or any(char not in "0123456789abcdef" for char in hex_digest):
            raise ValueError("invalid sha256 digest")
        return self.blob_root / hex_digest[:2] / hex_digest[2:]

    @staticmethod
    def _digest_from_uri(uri: str) -> str:
        prefix = "artifact://sha256/"
        if not uri.startswith(prefix):
            raise ValueError(f"unsupported artifact URI: {uri}")
        return f"sha256:{uri[len(prefix) :]}"

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            return
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            # Wrap the descriptor first so that it is always closed. mkstemp already creates the
            # file readable only by its owner; fchmod restates 0600 where it exists (POSIX).
            with os.fdopen(descriptor, "wb") as handle:
                if hasattr(os, "fchmod"):
                    os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if sys.platform == "win32":
                _move_into_place_on_windows(temporary, path, data)
            else:
                try:
                    os.replace(temporary, path)
                except FileExistsError:
                    temporary.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, default=str).encode(
        "utf-8"
    )


def _parses(data: bytes) -> bool:
    try:
        json.loads(data)
    except ValueError:
        return False
    return True


def _move_into_place_on_windows(temporary: Path, path: Path, data: bytes) -> None:
    """Move a written temporary file into place on Windows without disturbing readers.

    ``os.replace`` over an existing file is not invisible on Windows as ``rename`` is on POSIX:
    while ``MoveFileEx`` swaps the destination, another writer's move fails (``WinError 5``) and a
    reader's open fails (``Errno 13``). Concurrent stores of the same blob hit both, because the
    ``exists`` check before writing is a race. Blobs are named by the digest of their bytes, so:

    - the temporary file is moved with ``os.rename``, which never replaces an existing file;
    - a destination that already holds the bytes of ``data`` (same digest) is the store already
      done: the temporary file is dropped and the destination is left untouched;
    - a destination with other bytes (a descriptor with other metadata, a damaged blob) is never
      accepted: it is overwritten with ``os.replace``, as POSIX does.

    A failed move is retried with a short backoff; the last error is raised."""
    expected = sha256_bytes(data)
    delay = _WINDOWS_REPLACE_BACKOFF_SECONDS
    for attempt in range(_WINDOWS_REPLACE_ATTEMPTS):
        try:
            _move_once_on_windows(temporary, path, expected)
            return
        except OSError:
            if attempt == _WINDOWS_REPLACE_ATTEMPTS - 1:
                raise
        time.sleep(delay)
        delay *= 2


def _move_once_on_windows(temporary: Path, path: Path, expected: str) -> None:
    """One attempt of :func:`_move_into_place_on_windows`; raises ``OSError`` when it failed."""
    try:
        os.rename(temporary, path)
    except OSError:
        existing = _digest_of(path)
        if existing == expected:
            temporary.unlink(missing_ok=True)
            return
        if existing is None:
            raise
        # Other bytes, read in full: overwrite them as POSIX does, never accept them.
        os.replace(temporary, path)


def _digest_of(path: Path) -> str | None:
    """The digest of the bytes at ``path``; ``None`` when absent or not readable right now."""
    try:
        return sha256_bytes(path.read_bytes())
    except OSError:
        return None
