from __future__ import annotations

from governed_harness.domain.models import MemoryRecord
from governed_harness.evidence.hashing import sha256_json


def context_manifest(records: tuple[MemoryRecord, ...]) -> dict[str, object]:
    items = [
        {
            "memoryId": item.memory_id,
            "level": item.level,
            "key": item.key,
            "value": item.value,
            "provenance": item.provenance.model_dump(mode="json"),
        }
        for item in records
    ]
    return {"records": items, "digest": sha256_json(items)}
