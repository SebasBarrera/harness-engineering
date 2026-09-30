from __future__ import annotations

from pathlib import Path

import pytest

from governed_harness.evidence import LocalArtifactStore


def test_artifact_is_redacted_before_hashing(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    ref = store.put(b"Authorization: Bearer secret-token")
    assert b"secret-token" not in store.get(ref)
    assert ref.redacted


def test_artifact_tampering_is_detected(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    ref = store.put(b"evidence", redact=False)
    hex_digest = ref.digest.split(":", 1)[1]
    path = store.blob_root / hex_digest[:2] / hex_digest[2:]
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        store.get(ref)
    assert not store.verify(ref)
