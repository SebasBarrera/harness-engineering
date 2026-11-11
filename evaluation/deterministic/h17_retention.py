#!/usr/bin/env python3
"""H17: the evidence survives retention and garbage collection (``harness gc``),
``CATALOGO.md`` §2.2 and ``PREREGISTRO.md`` (third freeze, with amendment 2). No model is called.

Twenty runs in one workspace of the sample project under the retention ``harness init`` writes
(``artifactDays`` 30, ``eventDays`` 365, ``orphanArtifacts: true``). Each run is created with the
harness's wall clock moved back (``timeshift.py --hours -N`` for ``run start``, the waits and the
decision), so at the real time they have different ages:

* old (-400 days): 4 approved, 1 rejected, 1 open (waiting at DECISION);
* middle (-40 days): 4 approved, 1 cancelled, 1 rejected, 1 open;
* recent (-1 day): 5 approved, 1 rejected, 1 open;

plus 3 orphan artifacts (a blob and a descriptor in the store's format, modified 2 h ago: an
interrupted write, declared as such). Then ``harness gc`` (report), ``harness gc --apply``, an
inventory of the artifact store and of the runs before, between and after, ``harness verify
--run`` of every run, and ``harness export`` + ``harness verify --bundle`` of every kept run.

The **oracle** is the documented policy: ended old runs are removed, ended middle runs lose the
artifacts no kept run uses (their events and records stay), recent and open runs are untouched,
orphans are deleted. Apart from the criterion, the **forged-prune probe** deletes an artifact
only one recent run uses and appends a well-linked ``retention.artifacts.pruned`` event that
lists it, without re-anchoring; its control deletes one without the event. Which behaviour the
harness has for events after the anchored head (``governance.unanchoredEvents``, PR #154) is
read from the installed harness, recorded and compared, never assumed.

Usage:
  python evaluation/deterministic/h17_retention.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only G01]
      [--conditions H] [--reps 1] [--summarize-only] [--keep-work]

Writes ``DIR/h17-retention.jsonl``, ``DIR/h17-retention-summary.json``, ``DIR/environment.json``
and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_support import (  # noqa: E402
    decide,
    feature_spec,
    feature_task,
    harness_python,
    reset_tree,
    shifted,
)
from common import sha256_json  # noqa: E402
from resource_support import (  # noqa: E402
    Workspace,
    cleanup,
    governed,
    main,
    proportion,
    run_state,
    saved_config,
    stamp_dir,
)
from suite_support import start_run  # noqa: E402

PRUNED_EVENT = "retention.artifacts.pruned"
GROUP_HOURS = {"old": -400 * 24, "middle": -40 * 24, "recent": -24}
PLAN: list[tuple[str, str]] = [
    *[("old", "approved")] * 4,
    ("old", "rejected"),
    ("old", "open"),
    *[("middle", "approved")] * 4,
    ("middle", "cancelled"),
    ("middle", "rejected"),
    ("middle", "open"),
    *[("recent", "approved")] * 5,
    ("recent", "rejected"),
    ("recent", "open"),
]
ORPHANS = 3
CASES = [{"id": "G01"}]


def conditions_of(case: dict[str, Any]) -> list[str]:
    del case
    return ["H"]


# ----- which behaviour the installed harness has (PR #154) --------------------------------------
def schema_has_unanchored(args: argparse.Namespace) -> bool | None:
    """Whether the project-config schema of the installed package declares
    ``governance.unanchoredEvents``; ``None`` when it cannot be read."""
    code = (
        "import importlib.resources as r, sys;"
        "p = r.files('governed_harness') / 'resources' / 'schemas' / 'v1' / "
        "'project-config.schema.json';"
        "sys.stdout.write(p.read_text(encoding='utf-8'))"
    )
    try:
        proc = subprocess.run(
            [harness_python(args), "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        schema = json.loads(proc.stdout)
    except ValueError:
        return None
    return "unanchoredEvents" in json.dumps(schema)


def extra_environment(args: argparse.Namespace) -> dict[str, Any]:
    return {"unanchoredEventsInSchema": schema_has_unanchored(args)}


# ----- the artifact store and the registry ------------------------------------------------------
def store_root(ws: Path) -> Path:
    """The artifact store: under the state directory (``runtime.stateDir: auto``, the
    ``harness-state`` next to the workspace) or, without it, under ``.harness/``."""
    for base in (ws.parent / "harness-state" / "state", ws / ".harness"):
        found = next(iter(base.rglob("blobs/sha256")), None) if base.exists() else None
        if found is not None:
            return found.parent.parent
    raise RuntimeError("no artifact store in the state directory or under .harness/")


def inventory(ws: Path) -> dict[str, Any]:
    root = store_root(ws)
    blobs = {
        f"{path.parent.name}{path.name}": path.stat().st_size
        for path in (root / "blobs" / "sha256").glob("*/*")
        if path.is_file()
    }
    meta = sorted(path.stem for path in (root / "metadata").glob("*.json"))
    return {"blobs": blobs, "metadata": meta, "bytes": sum(blobs.values())}


def registry_runs(database: Path) -> list[str]:
    connection = sqlite3.connect(database)
    try:
        return sorted(
            row[0]
            for row in connection.execute(
                "SELECT record_id FROM records WHERE record_type='execution'"
            )
        )
    finally:
        connection.close()


def event_count(database: Path, run_id: str) -> int:
    connection = sqlite3.connect(database)
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE execution_id=?", (run_id,)
            ).fetchone()[0]
        )
    finally:
        connection.close()


def add_orphans(ws: Path) -> list[str]:
    """Blobs with descriptors nothing references (an interrupted write), 2 h old."""
    root = store_root(ws)
    made = []
    for index in range(ORPHANS):
        data = f"interrupted write {index} {uuid.uuid4().hex}\n".encode()
        hex_digest = hashlib.sha256(data).hexdigest()
        blob = root / "blobs" / "sha256" / hex_digest[:2] / hex_digest[2:]
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(data)
        descriptor = root / "metadata" / f"{hex_digest}.json"
        descriptor.write_bytes(
            json.dumps(
                {
                    "digest": f"sha256:{hex_digest}",
                    "sizeBytes": len(data),
                    "mediaType": "text/plain",
                    "redacted": False,
                    "metadata": {},
                },
                sort_keys=True,
                indent=2,
            ).encode()
        )
        old = time.time() - 2 * 3600
        for path in (blob, descriptor):
            os.utime(path, (old, old))
        made.append(hex_digest)
    return made


# ----- the corpus -------------------------------------------------------------------------------
def make_run(
    workspace: Workspace, args: argparse.Namespace, n: int, group: str, outcome: str
) -> dict[str, Any]:
    reset_tree(workspace.ws)
    workspace.write_spec(feature_spec(n), f"calls-{n:02d}.jsonl")
    h = shifted(workspace.h, args, GROUP_HOURS[group])
    started = start_run(h, workspace.ws, workspace.run_dir, feature_task("h17", n))
    run_id = started["runId"]
    state = run_state(workspace, run_id)
    entry: dict[str, Any] = {
        "n": n,
        "group": group,
        "outcome": outcome,
        "runId": run_id,
        "runExitCode": started["runExitCode"],
        "atDecision": state.get("phase") == "DECISION",
    }
    digest = str(state.get("changeSetDigest"))
    if state.get("phase") == "DECISION":
        if outcome == "approved":
            entry["decideExitCode"] = decide(
                h,
                workspace.ws,
                workspace.database,
                run_id,
                "APPROVE",
                digest,
                rationale="approved for the retention corpus",
                actor="human.reviewer",
            )["exitCode"]
        elif outcome == "rejected":
            entry["decideExitCode"] = decide(
                h,
                workspace.ws,
                workspace.database,
                run_id,
                "REJECT",
                digest,
                rationale="rejected for the retention corpus",
                actor="human.reviewer",
            )["exitCode"]
        elif outcome == "cancelled":
            code, _, _ = h.run(
                workspace.ws, "run", "cancel", "--path", ".", "--run", run_id, label="run cancel"
            )
            entry["cancelExitCode"] = code
    final = run_state(workspace, run_id)
    entry["status"] = final.get("runStatus")
    entry["phase"] = final.get("phase")
    reset_tree(workspace.ws)
    return entry


def gc(workspace: Workspace, apply: bool) -> tuple[int, Any]:
    command = ["gc", "--path", "."] + (["--apply"] if apply else [])
    code, report, _ = workspace.h.run(workspace.ws, *command, label="gc --apply" if apply else "gc")
    return code, report


def verify_run(workspace: Workspace, run_id: str) -> dict[str, Any]:
    code, report, _ = workspace.h.run(
        workspace.ws, "verify", "--path", ".", "--run", run_id, label="verify --run"
    )
    runs = report.get("runs") if isinstance(report, dict) else None
    first = runs[0] if isinstance(runs, list) and runs else report
    anchor = first.get("anchor") if isinstance(first, dict) else None
    problems = (
        {
            k: first.get(k)
            for k in ("artifactProblems", "recordProblems", "chainProblems", "reasons")
            if k in first
        }
        if isinstance(first, dict)
        else None
    )
    return {"exitCode": code, "anchor": anchor, "problems": problems}


def bundle_check(workspace: Workspace, run_id: str) -> dict[str, Any]:
    bundle = workspace.run_dir / "bundles" / f"{run_id}.tar.gz"
    bundle.parent.mkdir(exist_ok=True)
    code, _, _ = workspace.h.run(
        workspace.ws,
        "export",
        "--path",
        ".",
        "--run",
        run_id,
        "--bundle",
        str(bundle),
        label="export",
    )
    out: dict[str, Any] = {"exportExitCode": code}
    if bundle.exists():
        code, verified, _ = workspace.h.run(
            workspace.ws, "verify", "--bundle", str(bundle), label="verify --bundle"
        )
        out["verifyBundleExitCode"] = code
        out["valid"] = verified.get("valid") if isinstance(verified, dict) else None
        out["problems"] = verified.get("problems") if isinstance(verified, dict) else None
    return out


# ----- the oracle -------------------------------------------------------------------------------
def oracle(runs: list[dict[str, Any]]) -> dict[str, list[str]]:
    ended = {"approved", "rejected", "cancelled"}
    return {
        "removed": sorted(
            r["runId"] for r in runs if r["group"] == "old" and r["outcome"] in ended
        ),
        "pruned": sorted(
            r["runId"] for r in runs if r["group"] == "middle" and r["outcome"] in ended
        ),
        "untouched": sorted(
            r["runId"] for r in runs if r["group"] == "recent" or r["outcome"] == "open"
        ),
    }


# ----- the forged-prune probe -------------------------------------------------------------------
def diff_artifact(database: Path, run_id: str) -> str | None:
    """The diff of the run's ChangeSet: an artifact only that run's distinct change uses."""
    connection = sqlite3.connect(database)
    try:
        rows = list(
            connection.execute(
                "SELECT payload_json FROM records WHERE record_type='change_set' "
                "AND execution_id=?",
                (run_id,),
            )
        )
    finally:
        connection.close()
    for (payload,) in rows:
        ref = json.loads(payload).get("diffRef")
        if isinstance(ref, str):
            return ref
    return None


def delete_artifact(ws: Path, uri: str) -> bool:
    root = store_root(ws)
    hex_digest = uri.removeprefix("artifact://sha256/")
    deleted = False
    for path in (
        root / "blobs" / "sha256" / hex_digest[:2] / hex_digest[2:],
        root / "metadata" / f"{hex_digest}.json",
    ):
        if path.exists():
            path.unlink()
            deleted = True
    return deleted


def forge_pruned_event(database: Path, run_id: str, uri: str) -> dict[str, Any]:
    """Append a well-linked ``retention.artifacts.pruned`` event, as ``events/sqlite_store.py``
    computes it, without touching the anchor (an attacker with the registry, not the anchor)."""
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT execution_sequence, event_digest, actor_json FROM events "
            "WHERE execution_id=? ORDER BY execution_sequence DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        sequence, previous, actor_json = int(row[0]) + 1, row[1], row[2]
        payload = {"artifactRefs": [uri], "artifactDays": 30}
        envelope = {
            "schemaVersion": "1.0",
            "eventId": f"evt_{uuid.uuid4().hex}",
            "executionId": run_id,
            "phaseExecutionId": None,
            "causationId": None,
            "correlationId": None,
            "sequence": sequence,
            "eventType": PRUNED_EVENT,
            "occurredAt": datetime.now(UTC).isoformat(),
            "actor": json.loads(actor_json),
            "payload": payload,
            "previousEventDigest": previous,
            "redactionVersion": "1",
        }
        digest = sha256_json(envelope)
        connection.execute(
            "INSERT INTO events(event_id,execution_id,execution_sequence,event_type,occurred_at,"
            "actor_json,payload_json,phase_execution_id,causation_id,correlation_id,"
            "previous_digest,event_digest,redaction_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                envelope["eventId"],
                run_id,
                sequence,
                PRUNED_EVENT,
                envelope["occurredAt"],
                actor_json,
                json.dumps(payload, sort_keys=True),
                None,
                None,
                None,
                previous,
                digest,
                "1",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return {"sequence": sequence, "digest": digest}


# ----- the case ---------------------------------------------------------------------------------
def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    del case, rep
    run_dir = stamp_dir(args, "h17", condition)
    workspace = governed(run_dir, args, feature_spec(1))
    saved_config(args, condition, workspace)
    init_config = yaml.safe_load((workspace.ws / ".harness" / "project.yaml").read_text("utf-8"))
    unanchored = (init_config.get("governance") or {}).get("unanchoredEvents")
    schema = schema_has_unanchored(args)
    behaviour = {
        "initWrites": unanchored,
        "inSchema": schema,
        "supported": bool(unanchored) or bool(schema),
    }
    runs = [
        make_run(workspace, args, n, group, outcome)
        for n, (group, outcome) in enumerate(PLAN, start=1)
    ]
    orphans = add_orphans(workspace.ws)
    db = workspace.database
    before = inventory(workspace.ws)
    registry_before = registry_runs(db)
    events_before = {r["runId"]: event_count(db, r["runId"]) for r in runs}
    dry_code, dry = gc(workspace, apply=False)
    between = inventory(workspace.ws)
    registry_between = registry_runs(db)
    apply_code, applied = gc(workspace, apply=True)
    after = inventory(workspace.ws)
    registry_after = registry_runs(db)
    expected = oracle(runs)

    applied_report = applied if isinstance(applied, dict) else {}
    dry_report = dry if isinstance(dry, dict) else {}
    gone_artifacts = sorted(set(before["metadata"]) - set(after["metadata"]))
    gone_blobs = sorted(set(before["blobs"]) - set(after["blobs"]))
    listed = sorted(
        str(uri).removeprefix("artifact://sha256/")
        for uri in [
            *(applied_report.get("artifacts") or []),
            *(applied_report.get("orphans") or []),
        ]
    )
    gone_runs = sorted(set(registry_before) - set(registry_after))

    def comparable(report: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in report.items() if k != "applied"}

    for entry in runs:
        run_id = entry["runId"]
        entry["inRegistryAfter"] = run_id in registry_after
        entry["eventsBefore"] = events_before[run_id]
        entry["eventsAfter"] = event_count(db, run_id) if entry["inRegistryAfter"] else 0
        entry["verify"] = verify_run(workspace, run_id)
        if entry["inRegistryAfter"]:
            entry["bundle"] = bundle_check(workspace, run_id)
        entry["notExpired"] = entry["group"] in {"recent", "middle"} or entry["outcome"] == "open"
        entry["verifies"] = entry["verify"]["exitCode"] == 0

    checks = {
        "dryRunChangesNothing": before == between and registry_before == registry_between,
        "dryReportEqualsApplied": comparable(dry_report) == comparable(applied_report),
        "goneArtifactsAreListed": gone_artifacts == listed and gone_blobs == listed,
        "goneRunsAreListed": gone_runs == sorted(applied_report.get("removedRuns") or []),
        "removedMatchesOracle": sorted(applied_report.get("removedRuns") or [])
        == expected["removed"],
        "prunedMatchesOracle": sorted(applied_report.get("prunedRuns") or []) == expected["pruned"],
        "untouchedKeepTheirEvents": all(
            r["eventsAfter"] == r["eventsBefore"]
            for r in runs
            if r["runId"] in expected["untouched"]
        ),
        "orphansDeleted": set(orphans) <= set(gone_artifacts)
        and set(orphans)
        <= {str(u).removeprefix("artifact://sha256/") for u in applied_report.get("orphans") or []},
    }
    probe = forged_prune_probe(workspace, runs)
    record = {
        "unanchoredEvents": behaviour,
        "runs": runs,
        "orphansCreated": orphans,
        "oracle": expected,
        "gc": {
            "dryExitCode": dry_code,
            "applyExitCode": apply_code,
            "dryReport": dry_report,
            "applyReport": applied_report,
        },
        "store": {
            "bytesBefore": before["bytes"],
            "bytesAfter": after["bytes"],
            "artifactsBefore": len(before["metadata"]),
            "artifactsAfter": len(after["metadata"]),
            "goneArtifacts": len(gone_artifacts),
            "listedArtifacts": len(listed),
            "unlistedGone": sorted(set(gone_artifacts) - set(listed)),
            "listedNotGone": sorted(set(listed) - set(gone_artifacts)),
        },
        "registry": {"before": len(registry_before), "after": len(registry_after)},
        "checks": checks,
        "forgedPruneProbe": probe,
        "deviations": workspace.deviations,
        "steps": list(workspace.h.steps),
    }
    not_expired = [r for r in runs if r["notExpired"]]
    record["brief"] = (
        f"{sum(r['verifies'] for r in not_expired)}/{len(not_expired)} not expired verify; "
        f"only listed deleted: {checks['goneArtifactsAreListed'] and checks['goneRunsAreListed']}"
    )
    cleanup(run_dir, args.keep_work)
    return record


def forged_prune_probe(workspace: Workspace, runs: list[dict[str, Any]]) -> dict[str, Any]:
    recent = [
        r
        for r in runs
        if r["group"] == "recent" and r["outcome"] == "approved" and r["inRegistryAfter"]
    ]
    if len(recent) < 2:
        return {"evaluated": False, "reason": "fewer than two recent approved runs"}
    out: dict[str, Any] = {"evaluated": True}
    for name, entry, forge in (("probe", recent[0], True), ("control", recent[1], False)):
        uri = diff_artifact(workspace.database, entry["runId"])
        if uri is None:
            out[name] = {"evaluated": False, "reason": "no ChangeSet diff artifact"}
            continue
        deleted = delete_artifact(workspace.ws, uri)
        forged = forge_pruned_event(workspace.database, entry["runId"], uri) if forge else None
        out[name] = {
            "runId": entry["runId"],
            "artifact": uri,
            "deleted": deleted,
            "forgedEvent": forged,
            "verify": verify_run(workspace, entry["runId"]),
        }
    return out


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    summary: dict[str, Any] = {
        "suite": "H17",
        "title": "the evidence survives retention and garbage collection",
        "simulatedPerson": "human.reviewer decides (a declared simulation); "
        "human.reviewer-simulated answers the waits before DECISION",
    }
    if not ok:
        summary["criteria"] = {"note": "not evaluated: the apparatus failed", "met": False}
        return summary
    r = ok[0]
    runs = r["runs"]
    not_expired = [x for x in runs if x["notExpired"]]
    by_group = {
        group: {
            outcome: [
                {
                    k: x.get(k)
                    for k in (
                        "status",
                        "phase",
                        "inRegistryAfter",
                        "eventsBefore",
                        "eventsAfter",
                        "verifies",
                    )
                }
                | {
                    "verifyExitCode": x["verify"]["exitCode"],
                    "bundleValid": (x.get("bundle") or {}).get("valid"),
                }
                for x in runs
                if x["group"] == group and x["outcome"] == outcome
            ]
            for outcome in ("approved", "rejected", "cancelled", "open")
        }
        for group in ("old", "middle", "recent")
    }
    probe = r.get("forgedPruneProbe") or {}
    behaviour = r.get("unanchoredEvents") or {}
    expected_probe = 6 if behaviour.get("supported") else 0
    summary.update(
        {
            "unanchoredEvents": behaviour,
            "byGroup": by_group,
            "notExpiredVerify": proportion(
                sum(x["verifies"] for x in not_expired), len(not_expired)
            ),
            "keptBundlesValid": proportion(
                sum(1 for x in runs if (x.get("bundle") or {}).get("valid")),
                sum(1 for x in runs if x.get("bundle")),
            ),
            "store": r["store"],
            "registry": r["registry"],
            "checks": r["checks"],
            "gcExitCodes": [r["gc"]["dryExitCode"], r["gc"]["applyExitCode"]],
            "notAtDecisionWhenExpected": sorted(x["n"] for x in runs if not x["atDecision"]),
            "forgedPruneProbe": {
                "reportedApart": True,
                "behaviourRead": behaviour,
                "expectationFromCode": {"probe": expected_probe, "control": 6},
                "probeVerifyExitCode": (probe.get("probe") or {}).get("verify", {}).get("exitCode"),
                "controlVerifyExitCode": (probe.get("control") or {})
                .get("verify", {})
                .get("exitCode"),
                "probeAnchor": (probe.get("probe") or {}).get("verify", {}).get("anchor"),
            },
        }
    )
    only_listed = all(
        r["checks"][k]
        for k in (
            "dryRunChangesNothing",
            "dryReportEqualsApplied",
            "goneArtifactsAreListed",
            "goneRunsAreListed",
        )
    )
    summary["criteria"] = {
        "notExpiredVerify100": {
            "value": f"{sum(x['verifies'] for x in not_expired)}/{len(not_expired)}",
            "met": len(runs) == len(PLAN) and all(x["verifies"] for x in not_expired),
        },
        "onlyListedDeleted": {"value": only_listed, "met": only_listed},
        "oracleChecksReportedApart": {
            k: r["checks"][k]
            for k in (
                "removedMatchesOracle",
                "prunedMatchesOracle",
                "untouchedKeepTheirEvents",
                "orphansDeleted",
            )
        },
        "note": "Pre-registered in PREREGISTRO.md (H17, amendment 2); evaluated as is. The "
        "forged-prune probe is reported apart, against the behaviour read from the harness.",
    }
    return summary


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H17",
            title="the evidence survives retention and garbage collection",
            stem="h17-retention",
            cases=CASES,
            conditions_of=conditions_of,
            all_conditions=["H"],
            runner=runner,
            summarize=summarize,
            extra_environment=extra_environment,
        )
    )
