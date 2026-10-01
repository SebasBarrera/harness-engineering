from __future__ import annotations

from governed_harness.domain.models import MemoryRecord
from governed_harness.evidence.hashing import sha256_json

from .store import MemoryExclusion


def context_manifest(
    records: tuple[MemoryRecord, ...], exclusions: tuple[MemoryExclusion, ...] = ()
) -> dict[str, object]:
    """Manifest of the memory applied to an execution.

    The digest covers the records that entered the context. Excluded candidates are listed with
    their reason so that the absence of a record is auditable too. The value of a record marked
    as sensitive is withheld: the manifest is evidence and reaches the agent provider.
    """
    items = [
        {
            "memoryId": item.memory_id,
            "level": item.level,
            "key": item.key,
            "value": None if item.sensitive else item.value,
            "provenance": item.provenance.model_dump(mode="json", by_alias=True),
            **({"sensitive": True} if item.sensitive else {}),
        }
        for item in records
    ]
    excluded = [
        {
            "memoryId": item.memory_id,
            "level": item.level,
            "key": item.key,
            "reason": item.reason,
            **({"supersededBy": item.superseded_by} if item.superseded_by else {}),
        }
        for item in exclusions
    ]
    return {"records": items, "digest": sha256_json(items), "excluded": excluded}
