#!/usr/bin/env python3
"""H14: tampering with the event chain, the records and the artifacts of a closed run (25 kinds;
extends the N13 probes ``tamper-events``, ``chain-truncated`` and ``chain-rewritten``). No model
is called.

Threat model (declared, ``CATALOGO.md`` H14 «Sesgos»): an attacker with the user's write access
to the workspace's ``.harness/`` and to the run registry (``runtime.stateDir``), **not** to the
chain anchor (``governance.chainAnchor: file``, kept under ``$HARNESS_ANCHOR_DIR``). The kinds
that need more (writing or deleting the anchor, moving the clock of the machine that records the
decision) are run too and labelled ``threatModel: outside``: they are the limits of the design,
reported, never counted in the criterion.

Conditions:

* ``H``: the configuration ``harness init`` writes (``governance.chainAnchor: file``,
  ``governance.verifyRecords: true``);
* ``H-core``: the 1.0.0 file of ``conditions.py`` (no anchor, no ``verifyRecords``);
* ``H-no-anchor``: ``harness init`` without the ``governance.chainAnchor`` key.

Method: per condition one run of the sample project (``friction.py``'s pricing function) with the
fixture provider is driven to DECISION by the declared simulated person and approved by
``human.reviewer``; the run directory (workspace and run registry, anchors included) is copied
twice, while the run waits in DECISION and once it is closed. A control ``harness verify`` on the
untouched closed run must exit 0 (else the condition is ``notEvaluated``). Each kind then starts
from the closed copy, applies one manipulation (``KINDS`` below: what is done, the first event or
record it affects, and the reaction the 2.0.0 design predicts, written before the first run) and
records ``harness verify`` (detection: exit 6), ``harness trace`` and ``harness status``
(``eventChainValid``), and where the report places the first problem (localisation).

Usage:
  python evaluation/deterministic/event_tampering.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--harness-python PATH] [--only-conditions H,...]
      [--only-kinds a,b] [--wheel WHEEL]

Writes ``DIR/event-tampering.jsonl`` (one record per condition and kind, plus one control record
per condition), ``DIR/event-tampering-summary.json`` and ``DIR/environment.json``. Exits 0 when
the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    Harness,
    add_common_arguments,
    environment,
    sha256_json,
    stamp,
    state_database,
    write_json,
    write_jsonl,
)
from suite_support import (  # noqa: E402
    approve,
    bin_dir,
    commit_init,
    configure,
    find_blob,
    portable_env,
    rmtree,
    sample_change,
    sample_project,
    sample_task,
    start_run,
    status_of,
    write_fixture,
)

CONDITIONS = ("H", "H-core", "H-no-anchor")
DECISION_EVENT = "human.decision.recorded"


@dataclass
class Ctx:
    condition: str
    h: Harness
    ws: Path
    state_root: Path
    database: Path
    run_id: str
    snapshots: dict[str, Path]
    args: argparse.Namespace
    notes: dict[str, Any] = field(default_factory=dict)


# ----- SQLite helpers --------------------------------------------------------------------------
def connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def event_rows(database: Path, run_id: str) -> list[sqlite3.Row]:
    connection = connect(database)
    try:
        return list(
            connection.execute(
                "SELECT * FROM events WHERE execution_id=? ORDER BY execution_sequence", (run_id,)
            )
        )
    finally:
        connection.close()


def first_of(database: Path, run_id: str, event_type: str) -> sqlite3.Row:
    return next(r for r in event_rows(database, run_id) if r["event_type"] == event_type)


def envelope(row: sqlite3.Row | dict[str, Any], previous: str | None) -> dict[str, Any]:
    """The envelope the event store hashes (``events/sqlite_store.py:append``)."""
    return {
        "schemaVersion": "1.0",
        "eventId": row["event_id"],
        "executionId": row["execution_id"],
        "phaseExecutionId": row["phase_execution_id"],
        "causationId": row["causation_id"],
        "correlationId": row["correlation_id"],
        "sequence": row["execution_sequence"],
        "eventType": row["event_type"],
        "occurredAt": row["occurred_at"],
        "actor": json.loads(row["actor_json"]),
        "payload": json.loads(row["payload_json"]),
        "previousEventDigest": previous,
        "redactionVersion": row["redaction_version"],
    }


def rechain(database: Path, run_id: str, start: int) -> None:
    """Recompute ``previous_digest`` and ``event_digest`` of every event from sequence ``start``
    on, as the event store computes them: a well-linked forged chain."""
    connection = connect(database)
    rows = list(
        connection.execute(
            "SELECT * FROM events WHERE execution_id=? ORDER BY execution_sequence", (run_id,)
        )
    )
    previous: str | None = None
    with connection:
        for row in rows:
            if row["execution_sequence"] >= start:
                digest = sha256_json(envelope(row, previous))
                connection.execute(
                    "UPDATE events SET previous_digest=?, event_digest=? WHERE event_id=?",
                    (previous, digest, row["event_id"]),
                )
                previous = digest
            else:
                previous = row["event_digest"]
    connection.close()


def append_well_linked(database: Path, run_id: str, source: sqlite3.Row, **changes: Any) -> int:
    """Append a copy of ``source`` at the head of the chain with a new id and the digests the
    event store would compute; returns its sequence."""
    rows = event_rows(database, run_id)
    head = rows[-1]
    row = dict(source)
    row.update(
        {
            "event_id": f"evt_{'f' * 24}{len(rows):08x}",
            "execution_sequence": int(head["execution_sequence"]) + 1,
        }
    )
    row.update(changes)
    digest = sha256_json(envelope(row, head["event_digest"]))
    connection = connect(database)
    with connection:
        connection.execute(
            "INSERT INTO events(event_id,execution_id,execution_sequence,event_type,occurred_at,"
            "actor_json,payload_json,phase_execution_id,causation_id,correlation_id,"
            "previous_digest,event_digest,redaction_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["event_id"],
                row["execution_id"],
                row["execution_sequence"],
                row["event_type"],
                row["occurred_at"],
                row["actor_json"],
                row["payload_json"],
                row["phase_execution_id"],
                row["causation_id"],
                row["correlation_id"],
                head["event_digest"],
                digest,
                row["redaction_version"],
            ),
        )
    connection.close()
    return int(row["execution_sequence"])


def execute(database: Path, sql: str, params: tuple[Any, ...] = ()) -> int:
    connection = connect(database)
    with connection:
        changed = connection.execute(sql, params).rowcount
    connection.close()
    return int(changed)


def record_row(database: Path, run_id: str, record_type: str) -> sqlite3.Row:
    connection = connect(database)
    try:
        return next(
            iter(
                connection.execute(
                    "SELECT * FROM records WHERE record_type=? AND (execution_id=? OR record_id=?) "
                    "ORDER BY updated_at DESC",
                    (record_type, run_id, run_id),
                )
            )
        )
    finally:
        connection.close()


def update_record(database: Path, row: sqlite3.Row, edit: Callable[[dict[str, Any]], None]) -> None:
    payload = json.loads(row["payload_json"])
    edit(payload)
    execute(
        database,
        "UPDATE records SET payload_json=? WHERE record_type=? AND record_id=?",
        (json.dumps(payload, sort_keys=True), row["record_type"], row["record_id"]),
    )


def set_any(payload: dict[str, Any], names: tuple[str, ...], value: Any) -> str:
    """Set the first of ``names`` the payload has (snake_case or camelCase); return its name."""
    for name in names:
        if name in payload:
            payload[name] = value
            return name
    raise KeyError(f"none of {names} in {sorted(payload)}")


def edit_decision_payload(payload: dict[str, Any]) -> None:
    set_any(payload, ("rationale",), "approved by the lead after a security review")


def artifact_blob(ctx: Ctx) -> tuple[str, Path]:
    """An artifact the run references and its blob on disk."""
    connection = connect(ctx.database)
    try:
        rows = list(
            connection.execute(
                "SELECT payload_json FROM records WHERE record_type='artifact' AND execution_id=?",
                (ctx.run_id,),
            )
        )
    finally:
        connection.close()
    roots = [ctx.ws / ".harness", ctx.state_root]
    for (payload,) in rows:
        uri = str(json.loads(payload).get("uri", ""))
        if not uri.startswith("artifact://sha256/"):
            continue
        for root in roots:
            found = find_blob(root, uri)
            if found is not None:
                return uri, found
    raise FileNotFoundError("no artifact blob of the run was found")


def anchor_file(ctx: Ctx) -> Path | None:
    return next(iter((ctx.state_root / "anchors").glob("*.json")), None)


def database_files(database: Path) -> list[Path]:
    return [database.with_name(database.name + suffix) for suffix in ("", "-wal", "-shm")]


# ----- the 25 kinds ----------------------------------------------------------------------------
# Each manipulation returns its ground truth: what it changed and where (``expected``: the first
# event sequence a chain or anchor check should name, or the record or artifact).
def k_payload_decision(ctx: Ctx) -> dict[str, Any]:
    row = first_of(ctx.database, ctx.run_id, DECISION_EVENT)
    execute(
        ctx.database,
        "UPDATE events SET payload_json=replace(payload_json,'approve','approved!') WHERE event_id=?",
        (row["event_id"],),
    )
    return {"expected": {"sequence": row["execution_sequence"]}}


def k_payload_middle(ctx: Ctx) -> dict[str, Any]:
    row = first_of(ctx.database, ctx.run_id, "gate.evaluated")
    payload = json.loads(row["payload_json"])
    payload["status"] = "FAILED" if payload.get("status") == "PASSED" else "PASSED"
    execute(
        ctx.database,
        "UPDATE events SET payload_json=? WHERE event_id=?",
        (json.dumps(payload, sort_keys=True), row["event_id"]),
    )
    return {"expected": {"sequence": row["execution_sequence"]}}


def k_actor(ctx: Ctx) -> dict[str, Any]:
    row = first_of(ctx.database, ctx.run_id, DECISION_EVENT)
    actor = json.loads(row["actor_json"])
    actor["actorId" if "actorId" in actor else next(iter(actor))] = "human.lead"
    execute(
        ctx.database,
        "UPDATE events SET actor_json=? WHERE event_id=?",
        (json.dumps(actor, sort_keys=True), row["event_id"]),
    )
    return {"expected": {"sequence": row["execution_sequence"]}}


def k_timestamp(ctx: Ctx) -> dict[str, Any]:
    row = first_of(ctx.database, ctx.run_id, DECISION_EVENT)
    execute(
        ctx.database,
        "UPDATE events SET occurred_at=? WHERE event_id=?",
        ("2020-01-01T00:00:00+00:00", row["event_id"]),
    )
    return {"expected": {"sequence": row["execution_sequence"]}}


def k_delete_middle(ctx: Ctx) -> dict[str, Any]:
    rows = event_rows(ctx.database, ctx.run_id)
    victim = rows[len(rows) // 2]
    execute(ctx.database, "DELETE FROM events WHERE event_id=?", (victim["event_id"],))
    return {
        "expected": {"sequence": victim["execution_sequence"]},
        "deletedType": victim["event_type"],
    }


def k_reorder(ctx: Ctx) -> dict[str, Any]:
    rows = event_rows(ctx.database, ctx.run_id)
    a, b = rows[len(rows) // 2], rows[len(rows) // 2 + 1]
    connection = connect(ctx.database)
    with connection:
        connection.execute(
            "UPDATE events SET execution_sequence=-1 WHERE event_id=?", (a["event_id"],)
        )
        connection.execute(
            "UPDATE events SET execution_sequence=? WHERE event_id=?",
            (a["execution_sequence"], b["event_id"]),
        )
        connection.execute(
            "UPDATE events SET execution_sequence=? WHERE event_id=?",
            (b["execution_sequence"], a["event_id"]),
        )
    connection.close()
    return {"expected": {"sequence": a["execution_sequence"]}}


def k_truncate_tail(ctx: Ctx) -> dict[str, Any]:
    rows = event_rows(ctx.database, ctx.run_id)
    last = rows[-1]["execution_sequence"]
    execute(
        ctx.database,
        "DELETE FROM events WHERE execution_id=? AND execution_sequence>?",
        (ctx.run_id, last - 3),
    )
    return {
        "expected": {"sequence": last - 2},
        "deletedTypes": [r["event_type"] for r in rows[-3:]],
    }


def k_truncate_consistent(ctx: Ctx) -> dict[str, Any]:
    """Delete the last three events and the records they project (``PROJECTED_EVENTS`` of the
    harness under test), so the records check has nothing left to compare."""
    from governed_harness.orchestration.verification import PROJECTED_EVENTS

    rows = event_rows(ctx.database, ctx.run_id)[-3:]
    truth = k_truncate_tail(ctx)
    deleted: list[str] = []
    for row in rows:
        projected = PROJECTED_EVENTS.get(row["event_type"])
        if projected is None:
            continue
        record_type, _model, id_field = projected
        payload = json.loads(row["payload_json"])
        camel = id_field.split("_")[0] + "".join(p.title() for p in id_field.split("_")[1:])
        record_id = payload.get(id_field) or payload.get(camel)
        if record_id:
            execute(
                ctx.database,
                "DELETE FROM records WHERE record_type=? AND record_id=?",
                (record_type, record_id),
            )
            deleted.append(f"{record_type}:{record_id}")
    truth["deletedRecords"] = deleted
    return truth


def k_rewrite_recompute(ctx: Ctx) -> dict[str, Any]:
    row = first_of(ctx.database, ctx.run_id, DECISION_EVENT)
    payload = json.loads(row["payload_json"])
    edit_decision_payload(payload)
    execute(
        ctx.database,
        "UPDATE events SET payload_json=? WHERE event_id=?",
        (json.dumps(payload, sort_keys=True), row["event_id"]),
    )
    rechain(ctx.database, ctx.run_id, int(row["execution_sequence"]))
    return {"expected": {"sequence": row["execution_sequence"]}}


def k_duplicate(ctx: Ctx) -> dict[str, Any]:
    row = first_of(ctx.database, ctx.run_id, DECISION_EVENT)
    sequence = append_well_linked(ctx.database, ctx.run_id, row)
    return {"expected": {"sequence": sequence}}


def k_delete_all(ctx: Ctx) -> dict[str, Any]:
    count = execute(ctx.database, "DELETE FROM events WHERE execution_id=?", (ctx.run_id,))
    return {"expected": {"sequence": 1}, "deletedEvents": count}


def k_consistent_rewrite(ctx: Ctx) -> dict[str, Any]:
    """The decision's rationale rewritten in its event (chain recomputed) and in its record."""
    k_rewrite_recompute(ctx)
    row = first_of(ctx.database, ctx.run_id, DECISION_EVENT)
    update_record(
        ctx.database, record_row(ctx.database, ctx.run_id, "decision"), edit_decision_payload
    )
    return {"expected": {"sequence": row["execution_sequence"]}}


def k_record_decision_edit(ctx: Ctx) -> dict[str, Any]:
    row = record_row(ctx.database, ctx.run_id, "decision")
    update_record(ctx.database, row, edit_decision_payload)
    return {"expected": {"recordType": "decision", "recordId": row["record_id"]}}


def k_record_decision_delete(ctx: Ctx) -> dict[str, Any]:
    row = record_row(ctx.database, ctx.run_id, "decision")
    execute(
        ctx.database,
        "DELETE FROM records WHERE record_type='decision' AND record_id=?",
        (row["record_id"],),
    )
    return {"expected": {"recordType": "decision", "recordId": row["record_id"]}}


def k_record_gate(ctx: Ctx) -> dict[str, Any]:
    row = record_row(ctx.database, ctx.run_id, "gate")
    update_record(
        ctx.database,
        row,
        lambda p: set_any(p, ("status",), "FAILED" if p.get("status") == "PASSED" else "PASSED"),
    )
    return {"expected": {"recordType": "gate", "recordId": row["record_id"]}}


def k_record_execution(ctx: Ctx) -> dict[str, Any]:
    row = record_row(ctx.database, ctx.run_id, "execution")
    update_record(
        ctx.database,
        row,
        lambda p: set_any(p, ("changeSetDigest", "change_set_digest"), "sha256:" + "0" * 64),
    )
    return {"expected": {"recordType": "execution", "recordId": row["record_id"]}}


def k_record_finding_forged(ctx: Ctx) -> dict[str, Any]:
    connection = connect(ctx.database)
    try:
        template = next(
            iter(
                connection.execute(
                    "SELECT * FROM records WHERE record_type='finding' ORDER BY updated_at DESC"
                )
            ),
            None,
        )
    finally:
        connection.close()
    record_id = "finding_" + "e" * 32
    if template is not None:
        payload = json.loads(template["payload_json"])
        set_any(payload, ("findingId", "finding_id"), record_id)
        set_any(payload, ("executionId", "execution_id"), ctx.run_id)
        if "severity" in payload:
            payload["severity"] = "INFO"
    else:
        payload = {"findingId": record_id, "executionId": ctx.run_id}
    row = record_row(ctx.database, ctx.run_id, "execution")
    execute(
        ctx.database,
        "INSERT INTO records(record_type,record_id,execution_id,project_id,created_at,updated_at,"
        "payload_json) VALUES('finding',?,?,?,?,?,?)",
        (
            record_id,
            ctx.run_id,
            row["project_id"],
            row["created_at"],
            row["updated_at"],
            json.dumps(payload, sort_keys=True),
        ),
    )
    return {
        "expected": {"recordType": "finding", "recordId": record_id},
        "templateFound": template is not None,
    }


def k_record_phase(ctx: Ctx) -> dict[str, Any]:
    connection = connect(ctx.database)
    try:
        row = next(
            iter(
                connection.execute(
                    "SELECT * FROM records WHERE record_type='phase' AND execution_id=? "
                    "AND payload_json LIKE '%VERIFICATION%' ORDER BY updated_at",
                    (ctx.run_id,),
                )
            )
        )
    finally:
        connection.close()
    update_record(
        ctx.database,
        row,
        lambda p: set_any(p, ("status",), "FAILED" if p.get("status") == "PASSED" else "PASSED"),
    )
    return {"expected": {"recordType": "phase", "recordId": row["record_id"]}}


def k_artifact_edit(ctx: Ctx) -> dict[str, Any]:
    uri, blob = artifact_blob(ctx)
    blob.chmod(0o600)
    blob.write_bytes(blob.read_bytes() + b"\n")
    return {"expected": {"uri": uri}}


def k_artifact_delete(ctx: Ctx) -> dict[str, Any]:
    uri, blob = artifact_blob(ctx)
    blob.chmod(0o600)
    blob.unlink()
    return {"expected": {"uri": uri}}


def k_artifact_forged_prune(ctx: Ctx) -> dict[str, Any]:
    """The blob is deleted and a well-linked ``retention.artifacts.pruned`` event naming it is
    appended, as ``harness gc --apply`` records a pruned artifact."""
    uri, blob = artifact_blob(ctx)
    blob.chmod(0o600)
    blob.unlink()
    rows = event_rows(ctx.database, ctx.run_id)
    sequence = append_well_linked(
        ctx.database,
        ctx.run_id,
        rows[-1],
        event_type="retention.artifacts.pruned",
        payload_json=json.dumps({"artifactRefs": [uri], "reason": "retention"}, sort_keys=True),
    )
    return {"expected": {"uri": uri, "sequence": sequence}}


def k_anchor_delete_truncate(ctx: Ctx) -> dict[str, Any]:
    anchor = anchor_file(ctx)
    if anchor is not None:
        anchor.unlink()
    truth = k_truncate_consistent(ctx)
    truth["anchorFileExisted"] = anchor is not None
    return truth


def k_anchor_rewrite_truncate(ctx: Ctx) -> dict[str, Any]:
    truth = k_truncate_consistent(ctx)
    anchor = anchor_file(ctx)
    truth["anchorFileExisted"] = anchor is not None
    if anchor is not None:
        head = event_rows(ctx.database, ctx.run_id)[-1]
        data = json.loads(anchor.read_text(encoding="utf-8"))
        data["runs"][ctx.run_id].update(
            {"sequence": head["execution_sequence"], "eventDigest": head["event_digest"]}
        )
        anchor.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return truth


def k_state_older_copy(ctx: Ctx) -> dict[str, Any]:
    """The run registry replaced by its copy from while the run waited in DECISION."""
    relative = ctx.database.relative_to(ctx.ws.parent)
    older = ctx.snapshots["decision"] / relative
    before = len(event_rows(ctx.database, ctx.run_id))
    for target, source in zip(database_files(ctx.database), database_files(older), strict=True):
        target.unlink(missing_ok=True)
        if source.exists():
            shutil.copy2(source, target)
    return {
        "expected": {"sequence": len(event_rows(ctx.database, ctx.run_id)) + 1},
        "eventsBefore": before,
    }


def k_clock_shift(ctx: Ctx) -> dict[str, Any]:
    """From the copy taken in DECISION, the approval is recorded by a harness whose clock is one
    year behind (``timeshift.py --hours -8760``)."""
    restore(ctx, "decision")
    status = status_of(ctx.h, ctx.ws, ctx.run_id)
    digest = str((status.get("execution") or {}).get("changeSetDigest"))
    clock = Harness(ctx.args.harness_python, ctx.h.env)
    code, _, _ = clock.run(
        ctx.ws,
        str(Path(__file__).resolve().parent / "timeshift.py"),
        "--hours",
        "-8760",
        "--",
        "gate",
        "decide",
        "--path",
        ".",
        "--run",
        ctx.run_id,
        "--decision",
        "APPROVE",
        "--change-set-digest",
        digest,
        "--actor",
        "human.reviewer",
        "--rationale",
        "approve",
        label="gate decide APPROVE (clock -8760 h)",
    )
    rows = event_rows(ctx.database, ctx.run_id)
    times = [str(r["occurred_at"]) for r in rows]
    backwards = [
        rows[i]["execution_sequence"] for i in range(1, len(rows)) if times[i] < times[i - 1]
    ]
    decision = next((r for r in rows if r["event_type"] == DECISION_EVENT), None)
    return {
        "expected": {"sequence": decision["execution_sequence"] if decision else None},
        "shiftedDecideExitCode": code,
        "eventsBackInTime": backwards,
    }


@dataclass(frozen=True)
class Kind:
    name: str
    group: str
    action: Callable[[Ctx], dict[str, Any]]
    what: str
    threat: str
    # The reaction the 2.0.0 design predicts per condition (True: harness verify exits 6),
    # written from the code (orchestration/verification.py, events/) before the first run.
    predicted: dict[str, bool]
    why: str
    origin: str = "H14"


ALL = {c: True for c in CONDITIONS}
NONE = {c: False for c in CONDITIONS}
ANCHOR_ONLY = {"H": True, "H-core": False, "H-no-anchor": False}

KINDS: list[Kind] = [
    Kind(
        "event-payload-decision",
        "events",
        k_payload_decision,
        "Edit the payload of the decision event (the N13 tamper-events edit).",
        "inside",
        ALL,
        "The event digest no longer matches its envelope (check_chain).",
        "N13 tamper-events",
    ),
    Kind(
        "event-payload-middle",
        "events",
        k_payload_middle,
        "Flip the status in the payload of the gate.evaluated event.",
        "inside",
        ALL,
        "Event digest mismatch.",
    ),
    Kind(
        "event-actor",
        "events",
        k_actor,
        "Change the actor of the decision event to human.lead.",
        "inside",
        ALL,
        "The actor is part of the hashed envelope.",
    ),
    Kind(
        "event-timestamp",
        "events",
        k_timestamp,
        "Backdate occurred_at of the decision event to 2020-01-01.",
        "inside",
        ALL,
        "occurredAt is part of the hashed envelope.",
    ),
    Kind(
        "event-delete-middle",
        "events",
        k_delete_middle,
        "Delete the event in the middle of the chain.",
        "inside",
        ALL,
        "Sequence gap (check_chain).",
    ),
    Kind(
        "event-reorder",
        "events",
        k_reorder,
        "Swap the sequence numbers of two adjacent events in the middle.",
        "inside",
        ALL,
        "The previous digest of the first moved event does not link.",
    ),
    Kind(
        "chain-truncated",
        "events",
        k_truncate_tail,
        "Delete the last three events (the shorter chain stays well linked; N13).",
        "inside",
        ANCHOR_ONLY,
        "Only the anchor holds the old head (events/anchor.py); without it the chain is valid. "
        "The records check may still catch a deleted event that projects a record.",
        "N13 chain-truncated",
    ),
    Kind(
        "chain-truncated-consistent",
        "events",
        k_truncate_consistent,
        "Delete the last three events and the records they project (a truncation that leaves "
        "no record without its event).",
        "inside",
        ANCHOR_ONLY,
        "Only the anchor holds the old head; without it nothing differs. Added after the first "
        "smoke run showed that the three-event truncation also deletes an evidence event, which "
        "the records check catches, so chain-truncated alone does not test the anchor.",
    ),
    Kind(
        "chain-rewritten",
        "events",
        k_rewrite_recompute,
        "Rewrite the decision's rationale and recompute every later digest (N13).",
        "inside",
        ALL,
        "With the anchor: rewritten. Without it the decision record still differs from the "
        "forged event (records check).",
        "N13 chain-rewritten",
    ),
    Kind(
        "event-duplicate",
        "events",
        k_duplicate,
        "Append a well-linked copy of the decision event at the head of the chain.",
        "inside",
        NONE,
        "The anchor only proves the old head is still in the chain (eventsAfterAnchor); the "
        "copy projects the same decision record, so no record differs. Predicted limit.",
    ),
    Kind(
        "events-all-deleted",
        "events",
        k_delete_all,
        "Delete every event of the run.",
        "inside",
        ALL,
        "Every projected record loses its event (no-event); with the anchor, truncated.",
    ),
    Kind(
        "chain-rewritten-consistent",
        "events",
        k_consistent_rewrite,
        "Rewrite the decision in its event (digests recomputed) and in its record: a fully "
        "consistent rewrite.",
        "inside",
        ANCHOR_ONLY,
        "Catalogue hypothesis: only the anchor detects it (rewritten); without it, a limit.",
    ),
    Kind(
        "record-decision-edit",
        "records",
        k_record_decision_edit,
        "Edit the rationale of the decision record in SQLite.",
        "inside",
        ALL,
        "The record differs from the decision event (verifyRecords design).",
    ),
    Kind(
        "record-decision-delete",
        "records",
        k_record_decision_delete,
        "Delete the decision record.",
        "inside",
        ALL,
        "The decision event has no record (missing) and the execution points at it.",
    ),
    Kind(
        "record-gate-edit",
        "records",
        k_record_gate,
        "Flip the status of the gate record.",
        "inside",
        ALL,
        "Record differs from its event.",
    ),
    Kind(
        "record-execution-pointer",
        "records",
        k_record_execution,
        "Point the execution at another ChangeSet digest.",
        "inside",
        ALL,
        "The execution's changeSetDigest is not a digest of a changeset.created event.",
    ),
    Kind(
        "record-finding-forged",
        "records",
        k_record_finding_forged,
        "Insert a finding record that has no event.",
        "inside",
        ALL,
        "A projected record without its event (no-event).",
    ),
    Kind(
        "record-phase-status",
        "records",
        k_record_phase,
        "Flip the status of the VERIFICATION phase record.",
        "inside",
        ALL,
        "The phase record differs from its phase.completed event.",
    ),
    Kind(
        "artifact-blob-edit",
        "artifacts",
        k_artifact_edit,
        "Append a byte to a blob the run references.",
        "inside",
        ALL,
        "The artifact store checks the content against its digest.",
    ),
    Kind(
        "artifact-blob-delete",
        "artifacts",
        k_artifact_delete,
        "Delete a blob the run references.",
        "inside",
        ALL,
        "The artifact is missing.",
    ),
    Kind(
        "artifact-delete-forged-prune",
        "artifacts",
        k_artifact_forged_prune,
        "Delete a blob and append a well-linked retention.artifacts.pruned event naming it.",
        "inside",
        NONE,
        "A pruned artifact may be missing; a well-linked appended event passes the anchor. "
        "Predicted limit.",
    ),
    Kind(
        "anchor-deleted-truncated",
        "anchor",
        k_anchor_delete_truncate,
        "Delete the anchor file, then truncate consistently (chain-truncated-consistent).",
        "outside",
        NONE,
        "An absent anchor verifies (status absent); outside the threat model.",
    ),
    Kind(
        "anchor-rewritten-truncated",
        "anchor",
        k_anchor_rewrite_truncate,
        "Truncate consistently and rewrite the anchor to the new head.",
        "outside",
        NONE,
        "An attacker who writes the anchor defeats it (events/anchor.py: not tamper-proof).",
    ),
    Kind(
        "state-db-older-copy",
        "state",
        k_state_older_copy,
        "Replace the run registry with its copy from while the run waited in DECISION (the "
        "approval disappears).",
        "inside",
        ANCHOR_ONLY,
        "The older registry is self-consistent; only the anchor holds the later head.",
    ),
    Kind(
        "clock-shifted-decision",
        "clock",
        k_clock_shift,
        "Record the approval with the harness's clock one year behind.",
        "outside",
        NONE,
        "The chain records the time the machine gives; nothing compares times. Outside the "
        "threat model (the attacker controls the recording process).",
    ),
]


# ----- running ---------------------------------------------------------------------------------
def snapshot(ctx_dir: Path, ws: Path, state_root: Path, name: str) -> Path:
    target = ctx_dir / f"snapshot-{name}"
    rmtree(target)
    shutil.copytree(ws, target / ws.name, symlinks=True)
    shutil.copytree(state_root, target / state_root.name, symlinks=True)
    return target


def restore(ctx: Ctx, name: str) -> None:
    source = ctx.snapshots[name]
    for directory in (ctx.ws, ctx.state_root):
        rmtree(directory)
        shutil.copytree(source / directory.name, directory, symlinks=True)


def verify(ctx: Ctx) -> dict[str, Any]:
    h, ws = ctx.h, ctx.ws
    code, report, _ = h.run(ws, "verify", "--path", ".", "--run", ctx.run_id, label="verify")
    trace_code, _, _ = h.run(
        ws,
        "trace",
        "--path",
        ".",
        "--run",
        ctx.run_id,
        "--format",
        "json",
        "--output",
        str(ws.parent / "trace.json"),
        label="trace",
    )
    status_code, status, _ = h.run(ws, "status", "--path", ".", "--run", ctx.run_id, label="status")
    report = report if isinstance(report, dict) else {"raw": report}
    if isinstance(report.get("runs"), list) and report["runs"]:
        # harness verify reports a list of runs, also for one --run.
        report = report["runs"][0]
    execution = (status.get("execution") or {}) if isinstance(status, dict) else {}
    return {
        "verifyExitCode": code,
        "verifyValid": report.get("valid"),
        "eventChain": report.get("eventChain"),
        "anchor": {
            k: (report.get("anchor") or {}).get(k)
            for k in ("mode", "status", "sequence", "eventCount", "eventsAfterAnchor")
        },
        "recordProblems": ((report.get("records") or {}).get("problems") or [])[:20],
        "artifactProblems": ((report.get("artifacts") or {}).get("problems") or [])[:20],
        "traceExitCode": trace_code,
        "statusExitCode": status_code,
        "statusEventChainValid": status.get("eventChainValid")
        if isinstance(status, dict)
        else None,
        "statusRunStatus": execution.get("status"),
        "statusPhase": execution.get("currentPhase"),
    }


def localisation(truth: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    """Where the report places the first problem, against the ground truth."""
    expected = truth.get("expected") or {}
    chain = observed.get("eventChain") or {}
    anchor = observed.get("anchor") or {}
    reported: dict[str, Any] = {}
    if chain and chain.get("valid") is False:
        reported["sequence"] = (chain.get("headSequence") or 0) + 1
        reported["by"] = "eventChain"
    elif anchor.get("status") in {"truncated", "rewritten"}:
        reported["sequence"] = anchor.get("sequence")
        reported["by"] = f"anchor:{anchor.get('status')}"
    problems = observed.get("recordProblems") or []
    if problems:
        reported["records"] = [
            f"{p.get('recordType')}:{p.get('recordId')}:{p.get('problem')}" for p in problems
        ]
    artifacts = observed.get("artifactProblems") or []
    if artifacts:
        reported["artifacts"] = [f"{p.get('uri')}:{p.get('problem')}" for p in artifacts]
    match: bool | None = None
    if "recordId" in expected:
        match = any(
            f"{expected['recordType']}:{expected['recordId']}:" in item
            for item in reported.get("records", [])
        )
    elif "uri" in expected:
        match = any(
            item.startswith(expected["uri"] + ":") for item in reported.get("artifacts", [])
        )
    elif "sequence" in expected and "sequence" in reported:
        if str(reported.get("by", "")).startswith("anchor"):
            # The anchor names the head it holds: the first event the truncation removed or the
            # head the rewrite replaced lies at or before it.
            match = (
                expected["sequence"] is not None and expected["sequence"] <= reported["sequence"]
            )
        else:
            match = reported["sequence"] == expected["sequence"]
    elif "sequence" in expected and observed.get("verifyExitCode") == 6:
        match = False
    return {"expected": expected, "reported": reported, "match": match}


def prepare_condition(
    condition: str, args: argparse.Namespace
) -> tuple[Ctx | None, dict[str, Any]]:
    run_dir = (args.work / f"h14-{condition}-{stamp()}").resolve()
    ws = run_dir / "ws"
    ws.mkdir(parents=True)
    sample_project(ws)
    state_root = run_dir / "harness-state"
    h = Harness(args.harness, portable_env([bin_dir(args.project_venv)], state_root))
    h.run(ws, "init", "--path", ".", label="init")
    commit_init(ws)
    # The fixture's log must be a path the agent sandbox lets it write ($TMPDIR).
    log_dir = Path(args.log_root) if args.log_root else Path(tempfile.mkdtemp(prefix="h14-"))
    command = write_fixture(
        run_dir, {"log": str(log_dir / f"h14-{condition}-calls.jsonl"), "change": sample_change()}
    )

    def mutate(config: dict[str, Any]) -> None:
        if condition == "H-no-anchor":
            config.get("governance", {}).pop("chainAnchor", None)

    config, deviations = configure(ws, command, core=condition == "H-core", mutate=mutate)
    database = state_database(h, ws)
    started = start_run(h, ws, run_dir, sample_task("task_h14_discount"))
    setup: dict[str, Any] = {
        "condition": condition,
        "kind": "control",
        "deviations": deviations,
        "governance": {
            k: (config.get("governance") or {}).get(k) for k in ("chainAnchor", "verifyRecords")
        },
        "runExitCode": started["runExitCode"],
        "waits": started["waits"],
    }
    run_id = started["runId"]
    ctx = Ctx(condition, h, ws, state_root, database, run_id, {}, args)
    if started["runExitCode"] != 4:
        setup["notEvaluated"] = f"the run did not reach DECISION (exit {started['runExitCode']})"
        return None, setup
    ctx.snapshots["decision"] = snapshot(run_dir, ws, state_root, "decision")
    setup["approveExitCode"] = approve(h, ws, run_id)
    status = status_of(h, ws, run_id)
    setup["closedStatus"] = (status.get("execution") or {}).get("status")
    ctx.snapshots["closed"] = snapshot(run_dir, ws, state_root, "closed")
    setup["eventCount"] = len(event_rows(database, run_id))
    setup.update(verify(ctx))
    if setup["verifyExitCode"] != 0 or setup["closedStatus"] != "PASSED":
        setup["notEvaluated"] = "the untouched closed run does not verify (control failed)"
        return None, setup
    return ctx, setup


def run_kind(ctx: Ctx, kind: Kind) -> dict[str, Any]:
    restore(ctx, "closed")
    ctx.h.steps.clear()
    record: dict[str, Any] = {
        "condition": ctx.condition,
        "kind": kind.name,
        "group": kind.group,
        "origin": kind.origin,
        "what": kind.what,
        "threatModel": kind.threat,
        "predictedDetected": kind.predicted[ctx.condition],
        "why": kind.why,
    }
    try:
        record["truth"] = kind.action(ctx)
    except (KeyError, StopIteration, FileNotFoundError, sqlite3.Error, OSError) as error:
        record["notEvaluated"] = (
            f"the manipulation could not be applied: {type(error).__name__}: {error}"
        )
        return record
    observed = verify(ctx)
    record.update(observed)
    record["detected"] = observed["verifyExitCode"] == 6
    record["matchesPrediction"] = record["detected"] == kind.predicted[ctx.condition]
    record["localisation"] = localisation(record["truth"], observed)
    record["steps"] = list(ctx.h.steps)
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_condition: dict[str, Any] = {}
    for condition in CONDITIONS:
        rows = [r for r in records if r["condition"] == condition and r["kind"] != "control"]
        control = next(
            (r for r in records if r["condition"] == condition and r["kind"] == "control"), None
        )
        if control is None:
            continue
        evaluated = [r for r in rows if "notEvaluated" not in r]
        inside = [r for r in evaluated if r["threatModel"] == "inside"]
        by_condition[condition] = {
            "control": {
                k: control.get(k)
                for k in (
                    "verifyExitCode",
                    "closedStatus",
                    "notEvaluated",
                    "deviations",
                    "governance",
                )
            },
            "kinds": len(rows),
            "evaluated": len(evaluated),
            "notEvaluated": {r["kind"]: r["notEvaluated"] for r in rows if "notEvaluated" in r},
            "detected": sum(r["detected"] for r in evaluated),
            "insideThreatModel": {
                "kinds": len(inside),
                "detected": sum(r["detected"] for r in inside),
                "detectionRate": round(sum(r["detected"] for r in inside) / len(inside), 4)
                if inside
                else None,
                "notDetected": sorted(r["kind"] for r in inside if not r["detected"]),
            },
            "outsideThreatModelNotDetected": sorted(
                r["kind"] for r in evaluated if r["threatModel"] == "outside" and not r["detected"]
            ),
            "localisedCorrectly": sum(1 for r in evaluated if r["localisation"]["match"] is True),
            "localisationMismatches": sorted(
                r["kind"]
                for r in evaluated
                if r["detected"] and r["localisation"]["match"] is False
            ),
            "traceRefusedWhenDetected": sum(
                1 for r in evaluated if r["detected"] and r["traceExitCode"] != 0
            ),
            "differentFromPrediction": sorted(
                r["kind"] for r in evaluated if not r["matchesPrediction"]
            ),
            "byKind": {
                r["kind"]: {
                    "detected": r.get("detected"),
                    "predicted": r.get("predictedDetected"),
                    "threatModel": r.get("threatModel"),
                    "verifyExitCode": r.get("verifyExitCode"),
                    "traceExitCode": r.get("traceExitCode"),
                    "anchorStatus": (r.get("anchor") or {}).get("status"),
                    "chainError": (r.get("eventChain") or {}).get("error"),
                    "localised": (r.get("localisation") or {}).get("match"),
                    "notEvaluated": r.get("notEvaluated"),
                }
                for r in rows
            },
        }
    return {
        "threatModel": "attacker with write access to .harness/ and the run registry, not to the "
        "chain anchor; kinds labelled outside need more and are reported as limits",
        "detection": "harness verify --run exits 6",
        "byCondition": by_condition,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--project-venv", type=Path, default=Path(sys.executable).parent.parent)
    parser.add_argument("--harness-python", default="", help="python of the harness (timeshift)")
    parser.add_argument("--log-root", default="", help="directory for the fixture's call log")
    parser.add_argument("--only-conditions", default="")
    parser.add_argument("--only-kinds", default="")
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.work = (args.work or args.out / ".work").resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    if not args.harness_python:
        found = shutil.which("python", path=str(Path(args.harness).resolve().parent))
        args.harness_python = found or sys.executable
    records_path = args.out / "event-tampering.jsonl"
    if not args.summarize_only:
        wanted = args.only_conditions.split(",") if args.only_conditions else list(CONDITIONS)
        kinds = [k for k in KINDS if not args.only_kinds or k.name in args.only_kinds.split(",")]
        write_json(
            args.out / "environment.json",
            environment(
                args.harness,
                args.wheel,
                {
                    "suite": "H14 event tampering",
                    "conditions": wanted,
                    "kinds": [k.name for k in kinds],
                    "projectVenv": str(args.project_venv),
                    "platformDeviation": "runtime.agentSandbox off (Windows)"
                    if sys.platform == "win32"
                    else None,
                },
            ),
        )
        records: list[dict[str, Any]] = []
        for condition in wanted:
            ctx, control = prepare_condition(condition, args)
            records.append(control)
            print(
                condition,
                "control",
                control.get("verifyExitCode"),
                control.get("notEvaluated"),
                flush=True,
            )
            for kind in kinds:
                if ctx is None:
                    records.append(
                        {
                            "condition": condition,
                            "kind": kind.name,
                            "group": kind.group,
                            "threatModel": kind.threat,
                            "notEvaluated": "control failed: " + str(control.get("notEvaluated")),
                        }
                    )
                    continue
                record = run_kind(ctx, kind)
                records.append(record)
                print(
                    condition,
                    kind.name,
                    record.get("verifyExitCode"),
                    "detected" if record.get("detected") else "NOT detected",
                    record.get("notEvaluated") or "",
                    flush=True,
                )
        write_jsonl(records_path, records)
    records = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    write_json(args.out / "event-tampering-summary.json", summarize(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
