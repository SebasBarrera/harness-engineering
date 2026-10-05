"""Portable evidence bundle (``harness export --bundle``, ``harness verify --bundle``).

A run's evidence lives in ``.harness`` (SQLite and the artifact store) of the workspace where it
ran; a decision taken there cannot be checked on another machine or in CI. A bundle is a
``tar.gz`` archive with:

* ``manifest.json``: the run, its status, ChangeSet digest, decision, event count and chain head,
  and the SHA-256 and size of every other entry (``EvidenceBundleManifest``);
* ``events.jsonl``: the run's event chain;
* ``records.json``: the run's records by type, and its task;
* ``artifacts/<sha256>`` and ``artifacts/<sha256>.json``: every artifact the run's events and
  records reference, with its descriptor (artifacts are stored redacted).

``verify_bundle`` checks a bundle without the workspace: the entries against the manifest, the
event chain (sequence, links and digests) against the manifest's count and head, every artifact
against its digest and every decision record against the event that recorded it. A bundle is a
consistency check: anyone who can rewrite the whole archive can rewrite it consistently, so it
establishes integrity against accidental or partial changes, not authenticity."""

from __future__ import annotations

import io
import json
import tarfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from governed_harness import __version__
from governed_harness.domain.enums import DecisionKind
from governed_harness.domain.errors import ConfigurationError, PolicyViolationError
from governed_harness.domain.models import (
    BundleEntry,
    EvidenceBundleManifest,
    Execution,
    HumanDecision,
    utc_now,
)
from governed_harness.evidence.hashing import sha256_bytes, sha256_json

if TYPE_CHECKING:
    from governed_harness.orchestration.engine import EngineServices

MANIFEST = "manifest.json"
EVENTS = "events.jsonl"
RECORDS = "records.json"
ARTIFACT_PREFIX = "artifact://sha256/"
DECISION_EVENT = "human.decision.recorded"
PRUNED_EVENT = "retention.artifacts.pruned"
APPROVALS = frozenset({DecisionKind.APPROVE.value, DecisionKind.APPROVE_EXCEPTION.value})
MAX_MEMBER_BYTES = 512 * 1024 * 1024
_TAR_MTIME = 0  # deterministic member times


def artifact_refs(value: Any) -> set[str]:
    """Every artifact URI inside a JSON value."""
    found: set[str] = set()
    if isinstance(value, str):
        if value.startswith(ARTIFACT_PREFIX):
            found.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            found |= artifact_refs(item)
    elif isinstance(value, list):
        for item in value:
            found |= artifact_refs(item)
    return found


def _record_types(services: EngineServices, execution_id: str) -> list[str]:
    rows = services.state.connection.execute(
        "SELECT DISTINCT record_type FROM records WHERE execution_id=? ORDER BY record_type",
        (execution_id,),
    ).fetchall()
    return [str(row["record_type"]) for row in rows]


def _add(archive: tarfile.TarFile, name: str, data: bytes) -> BundleEntry:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = _TAR_MTIME
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(data))
    return BundleEntry(path=name, digest=sha256_bytes(data), size_bytes=len(data))


def export_bundle(services: EngineServices, execution_id: str, output: Path) -> dict[str, Any]:
    """Write the bundle of a run to ``output``; refuses a run whose event chain does not
    verify."""
    execution = services.state.get("execution", execution_id, Execution)
    try:
        services.events.verify_chain(execution_id)
    except ValueError as error:
        raise PolicyViolationError(
            f"the event chain of {execution_id} does not verify; not exported: {error}"
        ) from error
    events = services.events.list(execution_id)
    records: dict[str, list[dict[str, Any]]] = {
        record_type: services.state.list_dicts(record_type, execution_id=execution_id)
        for record_type in _record_types(services, execution_id)
    }
    records["task"] = [services.state.get_dict("task", execution.task_id)]
    flags = {
        str(row["key"]): str(row["value"])
        for row in services.state.connection.execute("SELECT key, value FROM flags").fetchall()
        if str(row["key"]).endswith(f":{execution_id}")
    }
    refs = artifact_refs([event.as_dict() for event in events]) | artifact_refs(records)
    refs |= artifact_refs(list(flags.values()))
    refs |= {execution.configuration_snapshot_ref, execution.workflow_ref}
    decisions = records.get("decision", [])
    head = events[-1].event_digest if events else None
    output.parent.mkdir(parents=True, exist_ok=True)
    entries: list[BundleEntry] = []
    missing: list[str] = []
    temporary = output.with_name(f".{output.name}.{time.monotonic_ns()}.partial")
    try:
        with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            entries.append(_add(archive, EVENTS, services.events.export_jsonl(execution_id)))
            entries.append(
                _add(
                    archive,
                    RECORDS,
                    json.dumps(records, sort_keys=True, indent=2, default=str).encode("utf-8"),
                )
            )
            for uri in sorted(refs):
                if not uri.startswith(ARTIFACT_PREFIX):
                    continue
                hex_digest = uri[len(ARTIFACT_PREFIX) :]
                try:
                    data = services.artifacts.get(uri)
                    descriptor = services.artifacts.describe(uri)
                except (OSError, ValueError):
                    missing.append(uri)
                    continue
                entries.append(_add(archive, f"artifacts/{hex_digest}", data))
                entries.append(
                    _add(
                        archive,
                        f"artifacts/{hex_digest}.json",
                        json.dumps(
                            {
                                "digest": descriptor.digest,
                                "sizeBytes": descriptor.size_bytes,
                                "mediaType": descriptor.media_type,
                                "redacted": descriptor.redacted,
                                "metadata": descriptor.metadata or {},
                            },
                            sort_keys=True,
                            indent=2,
                        ).encode("utf-8"),
                    )
                )
            manifest = EvidenceBundleManifest(
                execution_id=execution_id,
                project_id=execution.project_id,
                task_id=execution.task_id,
                status=execution.status,
                current_phase=execution.current_phase,
                change_set_digest=execution.change_set_digest,
                decision_id=execution.human_decision_id,
                event_count=len(events),
                event_chain_head=head,
                core_version=__version__,
                entries=tuple(entries),
            )
            _add(
                archive,
                MANIFEST,
                json.dumps(manifest.model_dump(mode="json", by_alias=True), indent=2).encode(),
            )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "bundle": str(output),
        "executionId": execution_id,
        "status": execution.status.value,
        "changeSetDigest": execution.change_set_digest,
        "decisions": [item.get("decision") for item in decisions],
        "eventCount": len(events),
        "eventChainHead": head,
        "entries": len(entries) + 1,
        "artifacts": sum(
            1
            for item in entries
            if item.path.startswith("artifacts/") and not item.path.endswith(".json")
        ),
        "missingArtifacts": missing,
        "sizeBytes": output.stat().st_size,
    }


@dataclass
class BundleContents:
    manifest: EvidenceBundleManifest | None = None
    files: dict[str, tuple[str, int]] = field(default_factory=dict)
    """Entry path -> (digest, size)."""
    data: dict[str, bytes] = field(default_factory=dict)
    """The content of manifest, events and records."""
    problems: list[str] = field(default_factory=list)


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _members(path: Path) -> Iterator[tuple[tarfile.TarInfo, tarfile.TarFile]]:
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            yield member, archive


def read_bundle(path: Path) -> BundleContents:
    contents = BundleContents()
    try:
        for member, archive in _members(path):
            if not member.isfile():
                contents.problems.append(f"entry {member.name!r} is not a regular file")
                continue
            if not _safe_name(member.name):
                contents.problems.append(f"entry {member.name!r} has an unsafe path")
                continue
            if member.size > MAX_MEMBER_BYTES:
                contents.problems.append(f"entry {member.name!r} is larger than allowed")
                continue
            handle = archive.extractfile(member)
            if handle is None:
                contents.problems.append(f"entry {member.name!r} cannot be read")
                continue
            data = handle.read()
            contents.files[member.name] = (sha256_bytes(data), len(data))
            if member.name in {MANIFEST, EVENTS, RECORDS}:
                contents.data[member.name] = data
    except (OSError, tarfile.TarError, EOFError) as error:
        raise ConfigurationError(f"cannot read the bundle {path}: {error}") from error
    raw = contents.data.get(MANIFEST)
    if raw is None:
        contents.problems.append("manifest.json is missing")
        return contents
    try:
        contents.manifest = EvidenceBundleManifest.model_validate_json(raw)
    except ValueError as error:
        contents.problems.append(f"manifest.json is not a bundle manifest: {error}")
    return contents


def _check_chain(lines: list[dict[str, Any]], execution_id: str) -> list[str]:
    problems: list[str] = []
    previous: str | None = None
    for index, event in enumerate(lines, start=1):
        if event.get("executionId") != execution_id:
            problems.append(f"event {index} belongs to another run")
        if event.get("sequence") != index:
            problems.append(f"event sequence gap at {index}")
        if event.get("previousEventDigest") != previous:
            problems.append(f"previous digest mismatch at event {index}")
        envelope = {key: value for key, value in event.items() if key != "eventDigest"}
        if sha256_json(envelope) != event.get("eventDigest"):
            problems.append(f"event digest mismatch at event {index}")
        previous = event.get("eventDigest")
    return problems


def decision_entries(events: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    found = []
    for event in events:
        if event.get("eventType") != DECISION_EVENT:
            continue
        try:
            decision = HumanDecision.model_validate(event.get("payload") or {})
        except ValueError:
            continue
        found.append(
            {
                "decisionId": decision.decision_id,
                "decision": decision.decision.value,
                "changeSetDigest": decision.change_set_digest,
                "actorId": decision.actor.actor_id,
                "decidedAt": decision.decided_at.isoformat(),
                "expiresAt": decision.expires_at.isoformat() if decision.expires_at else None,
                "expired": decision.expires_at is not None and decision.expires_at <= now,
                "approval": decision.decision.value in APPROVALS,
            }
        )
    return found


def verify_bundle(path: Path, now: datetime | None = None) -> dict[str, Any]:
    """Verify a bundle without the workspace; ``valid`` is false on any problem."""
    contents = read_bundle(path)
    problems = list(contents.problems)
    manifest = contents.manifest
    decisions: list[dict[str, Any]] = []
    if manifest is not None:
        listed = {entry.path: entry for entry in manifest.entries}
        for name, entry in listed.items():
            actual = contents.files.get(name)
            if actual is None:
                problems.append(f"entry {name} is listed but missing")
            elif actual != (entry.digest, entry.size_bytes):
                problems.append(f"entry {name} does not match its digest or size")
        for name in contents.files:
            if name != MANIFEST and name not in listed:
                problems.append(f"entry {name} is not listed in the manifest")
        for name, (digest, _size) in contents.files.items():
            is_artifact = name.startswith("artifacts/") and not name.endswith(".json")
            if is_artifact and digest != f"sha256:{name.removeprefix('artifacts/')}":
                problems.append(f"artifact {name} does not match its name")
        events: list[dict[str, Any]] = []
        try:
            events = [
                json.loads(line)
                for line in contents.data.get(EVENTS, b"").decode("utf-8").splitlines()
                if line.strip()
            ]
        except (ValueError, UnicodeDecodeError) as error:
            problems.append(f"events.jsonl cannot be read: {error}")
        problems.extend(_check_chain(events, manifest.execution_id))
        if len(events) != manifest.event_count:
            problems.append(
                f"the bundle holds {len(events)} event(s); the manifest says {manifest.event_count}"
            )
        head = events[-1].get("eventDigest") if events else None
        if head != manifest.event_chain_head:
            problems.append("the last event is not the chain head of the manifest")
        decisions = decision_entries(events, now or utc_now())
        try:
            records = json.loads(contents.data.get(RECORDS, b"{}"))
        except ValueError as error:
            records = {}
            problems.append(f"records.json cannot be read: {error}")
        recorded = {item["decisionId"]: item for item in decisions}
        for raw in records.get("decision", []):
            try:
                record = HumanDecision.model_validate(raw)
            except ValueError:
                problems.append("a decision record cannot be read")
                continue
            event = recorded.get(record.decision_id)
            if event is None or (event["decision"], event["changeSetDigest"]) != (
                record.decision.value,
                record.change_set_digest,
            ):
                problems.append(f"decision record {record.decision_id} has no matching event")
        present = {
            name.removeprefix("artifacts/")
            for name in contents.files
            if name.startswith("artifacts/") and not name.endswith(".json")
        }
        referenced = artifact_refs(events) | artifact_refs(records)
        # Artifacts that harness gc pruned under retention.artifactDays are recorded as such.
        pruned = {
            str(uri)
            for event in events
            if event.get("eventType") == PRUNED_EVENT
            for uri in (event.get("payload") or {}).get("artifactRefs", [])
        }
        missing = sorted(
            uri for uri in referenced - pruned if uri.removeprefix(ARTIFACT_PREFIX) not in present
        )
        if missing:
            problems.append(f"{len(missing)} referenced artifact(s) are not in the bundle")
    return {
        "valid": not problems,
        "bundle": str(path),
        "executionId": manifest.execution_id if manifest else None,
        "taskId": manifest.task_id if manifest else None,
        "status": manifest.status.value if manifest else None,
        "changeSetDigest": manifest.change_set_digest if manifest else None,
        "eventCount": manifest.event_count if manifest else None,
        "eventChainHead": manifest.event_chain_head if manifest else None,
        "decisions": decisions,
        "problems": problems,
    }
