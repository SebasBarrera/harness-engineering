from pathlib import Path

from governed_harness.evidence.artifact_store import LocalArtifactStore


def test_roundtrip_and_deduplication(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    first = store.put(b"evidence")
    second = store.put(b"evidence")
    assert first.digest == second.digest
    assert store.get(first) == b"evidence"
