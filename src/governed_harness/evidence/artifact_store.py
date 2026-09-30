from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hashing import sha256_bytes
from .redaction import SecretRedactor


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
        digest = sha256_bytes(stored)
        hex_digest = digest.split(":", 1)[1]
        path = self._blob_path(hex_digest)
        self._atomic_write(path, stored)
        meta = dict(metadata or {})
        if redaction and redaction.redacted:
            meta["redactionRuleIds"] = list(redaction.rule_ids)
        descriptor = {
            "digest": digest,
            "sizeBytes": len(stored),
            "mediaType": media_type,
            "redacted": bool(redaction and redaction.redacted),
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
            redacted=descriptor["redacted"],
            metadata=meta,
        )

    def put_json(self, value: Any, *, metadata: dict[str, Any] | None = None) -> ArtifactRef:
        data = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        return self.put(data, media_type="application/json", metadata=metadata)

    def get(self, reference: ArtifactRef | str) -> bytes:
        digest = reference.digest if isinstance(reference, ArtifactRef) else self._digest_from_uri(reference)
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

    def _blob_path(self, hex_digest: str) -> Path:
        if len(hex_digest) != 64 or any(char not in "0123456789abcdef" for char in hex_digest):
            raise ValueError("invalid sha256 digest")
        return self.blob_root / hex_digest[:2] / hex_digest[2:]

    @staticmethod
    def _digest_from_uri(uri: str) -> str:
        prefix = "artifact://sha256/"
        if not uri.startswith(prefix):
            raise ValueError(f"unsupported artifact URI: {uri}")
        return f"sha256:{uri[len(prefix):]}"

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            return
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.replace(temporary, path)
            except FileExistsError:
                temporary.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)
