from __future__ import annotations

import os
import tempfile
from pathlib import Path

from governed_harness.capabilities.authorizer import (
    CapabilityAuthorizer,
    CapabilityDenied,
    contained_path,
)
from governed_harness.domain.models import Actor, CapabilityGrant, FilePatch
from governed_harness.evidence.hashing import sha256_file


class PatchApplier:
    def __init__(self, workspace: Path, authorizer: CapabilityAuthorizer | None = None) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.authorizer = authorizer or CapabilityAuthorizer()

    def apply(self, patch: FilePatch, *, actor: Actor, grants: list[CapabilityGrant]) -> Path:
        self.authorizer.authorize(
            actor=actor,
            capability="filesystem.write",
            resource=patch.path,
            grants=grants,
        )
        path = contained_path(self.workspace, Path(patch.path))
        if patch.expected_sha256 is not None and (
            not path.exists() or sha256_file(path) != patch.expected_sha256
        ):
            raise CapabilityDenied(f"precondition digest mismatch for {patch.path}")
        if patch.operation == "delete":
            if not any(grant.conditions.get("allowDelete") for grant in grants if grant.capability == "filesystem.write"):
                raise CapabilityDenied("delete requires an explicit allowDelete grant condition")
            path.unlink(missing_ok=False)
            return path
        if patch.operation == "create" and path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing == (patch.content or ""):
                return path
            raise FileExistsError(path)
        if patch.operation == "replace" and not path.exists():
            raise FileNotFoundError(path)
        if patch.operation == "append":
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(patch.content or "")
                handle.flush()
                os.fsync(handle.fileno())
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, (patch.content or "").encode("utf-8"))
        return path

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
