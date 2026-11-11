#!/usr/bin/env python3
"""H13: reconstruct "who approved which digest" without the workspace, ``CATALOGO.md`` §2.2 and
``PREREGISTRO.md`` (third freeze, with amendment 1). No model is called.

Thirty runs on the sample project, one fresh workspace each, decided by one of three simulated
people (a declared simulation): approved (3 of them acknowledging a new dependency), rejected (4
with the gate ``PASSED``, 4 with the gate ``FAILED`` on an untraced requirement), approved with an
exception over an untraced requirement (amendment 3), and approved with ``--no-continue`` and continued 73 h
later (the decision expired). Every run carries a configuration marker of its own
(``retention.artifactDays`` = 1000 + n).

Two conditions answer the same eight audit questions (actor, digest, time, rationale,
acknowledged risks, who changed what, configuration version, validators that passed) against the
truth the script seeded:

* ``H``: ``harness export`` of the run; the answers come from the bundle file **only**, and
  ``harness verify --bundle`` runs in an empty directory with an empty state directory;
* ``D*``: the same change in a Git repository (a commit by ``agent.fixture`` on ``change/NN``, a
  ``git merge --no-ff`` by the person for the approvals, no merge for the rejections) plus the
  log of ``python -m pytest -q`` on the change; the answers come from ``git`` commands and that
  log only. D* is not an agent nor a real review process.

Three questions that favour the direct condition (commit author, message, base revision) are
answered too and reported apart.

Usage:
  python evaluation/deterministic/h13_bundle_audit.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only A01,...]
      [--conditions H,D*] [--reps 1] [--summarize-only] [--keep-work]

Writes ``DIR/h13-bundle-audit.jsonl``, ``DIR/h13-bundle-audit-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_support import (  # noqa: E402
    decide,
    email_person,
    feature_files,
    feature_spec,
    feature_task,
    git_as,
    head,
    shifted,
)
from resource_support import (  # noqa: E402
    Utf8Harness,
    cleanup,
    environment_python,
    governed,
    main,
    proportion,
    run_state,
    run_task,
    saved_config,
    stamp_dir,
)
from suite_support import bin_dir, portable_env, rmtree, sample_project  # noqa: E402

PEOPLE = ("human.ana", "human.ben", "human.cora")
QUESTIONS = (
    "actor",
    "digest",
    "time",
    "rationale",
    "acknowledgedRisks",
    "whoChangedWhat",
    "configurationVersion",
    "validators",
)
EXTRA_QUESTIONS = ("commitAuthor", "commitMessage", "baseRevision")
PRODUCER = {"H": "fixture", "D*": "agent.fixture"}
TIME_SLACK = timedelta(seconds=1)


def case(case_id: str, n: int, kind: str, **flags: bool) -> dict[str, Any]:
    return {"id": case_id, "n": n, "kind": kind, "person": PEOPLE[n % 3], **flags}


CASES: list[dict[str, Any]] = [
    *(case(f"A{i:02d}", i, "approved", risk=i >= 6) for i in range(1, 9)),
    *(case(f"R{i:02d}", 8 + i, "rejected", untraced=i >= 5) for i in range(1, 9)),
    *(case(f"X{i:02d}", 16 + i, "exception", untraced=True) for i in range(1, 8)),
    *(case(f"V{i:02d}", 23 + i, "expired") for i in range(1, 8)),
]


def rationale_of(c: dict[str, Any]) -> str:
    return f"{c['kind']} {c['id']}: decided by {c['person']} after reading the brief"


def conditions_of(c: dict[str, Any]) -> list[str]:
    del c
    return ["H", "D*"]


def marker(n: int) -> int:
    return 1000 + n


# ----- the independent test run (truth of question 8 and D*'s CI log) -------------------------
def independent_pytest(root: Path, args: argparse.Namespace) -> tuple[bool, str]:
    proc = subprocess.run(
        [environment_python(args), "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-4000:]


# ----- H: run, decide, export -------------------------------------------------------------------
def run_h(c: dict[str, Any], args: argparse.Namespace, run_dir: Path) -> dict[str, Any]:
    n = c["n"]

    def condition(config: dict[str, Any]) -> None:
        config.setdefault("retention", {})["artifactDays"] = marker(n)

    spec = feature_spec(n, pickle=bool(c.get("pickle")), risk=bool(c.get("risk")))
    workspace = governed(run_dir, args, spec, mutate=condition)
    saved_config(args, "H", workspace)
    ws, h = workspace.ws, workspace.h
    base = head(ws)
    task = feature_task("h13", n, untraced=bool(c.get("untraced")), risk=bool(c.get("risk")))
    started = run_task(workspace, task)
    run_id = started["runId"]
    state = run_state(workspace, run_id)
    truth: dict[str, Any] = {
        "actor": c["person"],
        "rationale": rationale_of(c),
        "acknowledgedRisks": ["newDependency"] if c.get("risk") else [],
        "files": sorted(feature_files(n, pickle=bool(c.get("pickle")), risk=bool(c.get("risk")))),
        "producer": PRODUCER["H"],
        "marker": marker(n),
    }
    record: dict[str, Any] = {
        "runId": run_id,
        "runExitCode": started["runExitCode"],
        "waits": started["waits"],
        "atDecision": state,
        "deviations": workspace.deviations,
    }
    if state.get("phase") != "DECISION":
        record["reachedDecision"] = False
        record["truth"] = truth
        return record
    record["reachedDecision"] = True
    digest = str(state.get("changeSetDigest"))
    truth["digest"] = digest
    db = workspace.database
    if c["kind"] == "approved":
        extra = ("--acknowledge-risk", "newDependency") if c.get("risk") else ()
        decision = decide(
            h,
            ws,
            db,
            run_id,
            "APPROVE",
            digest,
            rationale=rationale_of(c),
            actor=c["person"],
            extra=extra,
        )
    elif c["kind"] == "rejected":
        decision = decide(
            h, ws, db, run_id, "REJECT", digest, rationale=rationale_of(c), actor=c["person"]
        )
    elif c["kind"] == "exception":
        decision = decide(
            h,
            ws,
            db,
            run_id,
            "APPROVE_EXCEPTION",
            digest,
            rationale=rationale_of(c),
            actor=c["person"],
            extra=(
                "--expires-in",
                "14d",
                "--alternative-evidence",
                "the loader only reads files the service wrote",
                "--follow-up",
                f"issue {c['id']}",
            ),
        )
    else:
        decision = decide(
            h,
            ws,
            db,
            run_id,
            "APPROVE",
            digest,
            rationale=rationale_of(c),
            actor=c["person"],
            extra=("--no-continue",),
        )
        code, _, _ = shifted(h, args, 73).run(
            ws, "run", "continue", "--path", ".", "--run", run_id, label="run continue"
        )
        record["continueExitCode"] = code
    truth["window"] = [decision["startedAt"], decision["finishedAt"]]
    record["decision"] = {k: v for k, v in decision.items() if k != "output"}
    record["decisionOutput"] = decision["output"]
    record["final"] = run_state(workspace, run_id)
    # The independent test run on the same change (the truth of question 8).
    check = run_dir / "independent"
    check.mkdir()
    sample_project(check)
    for relative, content in feature_files(
        n, pickle=bool(c.get("pickle")), risk=bool(c.get("risk"))
    ).items():
        (check / relative).parent.mkdir(parents=True, exist_ok=True)
        (check / relative).write_bytes(content.encode("utf-8"))
    truth["testsPassed"], _ = independent_pytest(check, args)
    # The closure commit (approved and exception runs) is the commit that carries the change.
    branch = f"harness/{run_id}"
    closure = git_as(ws, "rev-parse", "--verify", "-q", branch, check=False).strip()
    if closure:
        truth["commitAuthor"] = git_as(ws, "log", "-1", "--format=%an <%ae>", branch).strip()
        truth["commitMessage"] = git_as(ws, "log", "-1", "--format=%B", branch).strip()
        truth["baseRevision"] = base
    bundle = run_dir / "bundle.tar.gz"
    code, exported, _ = h.run(
        ws, "export", "--path", ".", "--run", run_id, "--bundle", str(bundle), label="export"
    )
    record["exportExitCode"] = code
    record["export"] = (
        {k: exported.get(k) for k in ("entries", "artifacts", "missingArtifacts", "sizeBytes")}
        if isinstance(exported, dict)
        else exported
    )
    # verify-approval (reported apart): the closure commit, or for the expired runs a commit
    # of the working tree that holds the decided change.
    if c["kind"] in {"approved", "exception", "expired"}:
        target = branch if closure else ""
        if not closure and c["kind"] == "expired":
            git_as(ws, "checkout", "-q", "-b", f"audit/{run_id}")
            git_as(ws, "add", "-A")
            git_as(ws, "commit", "-qm", "the decided change")
            target = f"audit/{run_id}"
        if target and bundle.exists():
            code, approval, _ = h.run(
                ws,
                "verify-approval",
                "--path",
                ".",
                "--base",
                base,
                "--head",
                target,
                "--bundle",
                str(bundle),
                "--no-workspace",
                label="verify-approval",
            )
            record["verifyApprovalExitCode"] = code
            record["verifyApproval"] = approval if isinstance(approval, dict) else None
            if c["kind"] == "expired":
                # Amendment 3: the decision is expired only on the moved clock.
                code, _, _ = shifted(h, args, 73).run(
                    ws,
                    "verify-approval",
                    "--path",
                    ".",
                    "--base",
                    base,
                    "--head",
                    target,
                    "--bundle",
                    str(bundle),
                    "--no-workspace",
                    label="verify-approval",
                )
                record["verifyApprovalShiftedExitCode"] = code
    record["truth"] = truth
    # Without the workspace: the bundle alone, verified in an empty directory.
    auditor = run_dir / "auditor"
    auditor.mkdir()
    if bundle.exists():
        copy = auditor / "bundle.tar.gz"
        shutil.copyfile(bundle, copy)
        rmtree(ws)
        empty = Utf8Harness(
            args.harness, portable_env([bin_dir(args.project_venv)], run_dir / "auditor-state")
        )
        code, verified, _ = empty.run(auditor, "verify", "--bundle", str(copy), label="verify")
        h.steps.extend(empty.steps)
        record["verifyBundleExitCode"] = code
        record["verifyBundle"] = (
            {
                "valid": verified.get("valid"),
                "problems": verified.get("problems"),
                "decisions": verified.get("decisions"),
            }
            if isinstance(verified, dict)
            else verified
        )
        if c["kind"] == "expired":
            moved = shifted(empty, args, 73)
            code, verified, _ = moved.run(auditor, "verify", "--bundle", str(copy), label="verify")
            h.steps.extend(moved.steps)
            record["verifyBundleShifted"] = {
                "exitCode": code,
                "decisions": verified.get("decisions") if isinstance(verified, dict) else None,
            }
        record["answers"] = answer_from_bundle(copy, c)
    return record


# ----- H: the answers from the bundle file alone -----------------------------------------------
def read_bundle(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"artifacts": {}}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            data = handle.read()
            if member.name == "manifest.json":
                out["manifest"] = json.loads(data)
            elif member.name == "events.jsonl":
                out["events"] = [
                    json.loads(line) for line in data.decode("utf-8").splitlines() if line
                ]
            elif member.name == "records.json":
                out["records"] = json.loads(data)
            elif member.name.startswith("artifacts/") and not member.name.endswith(".json"):
                out["artifacts"][member.name.removeprefix("artifacts/")] = data
    return out


def dicts(value: Any) -> list[dict[str, Any]]:
    """Every dictionary inside a JSON value."""
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for item in value.values():
            found.extend(dicts(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(dicts(item))
    return found


def camel(value: Any) -> Any:
    """Event payloads are stored with snake_case keys, records with camelCase: one spelling."""
    if isinstance(value, dict):
        return {
            re.sub(r"_([a-z])", lambda m: m.group(1).upper(), str(k)): camel(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [camel(item) for item in value]
    return value


def artifact(bundle: dict[str, Any], uri: Any) -> bytes | None:
    if not isinstance(uri, str) or not uri.startswith("artifact://sha256/"):
        return None
    return bundle["artifacts"].get(uri.removeprefix("artifact://sha256/"))


def find_marker(data: bytes | None) -> int | None:
    """``retention.artifactDays`` of a configuration snapshot (JSON or YAML)."""
    if data is None:
        return None
    try:
        loaded = yaml.safe_load(data.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        loaded = None
    for item in dicts(loaded):
        retention = item.get("retention")
        if isinstance(retention, dict) and isinstance(retention.get("artifactDays"), int):
            return int(retention["artifactDays"])
    match = re.search(rb"artifactDays\"?\s*[:=]\s*(\d+)", data)
    return int(match.group(1)) if match else None


def answer_from_bundle(path: Path, c: dict[str, Any]) -> dict[str, Any]:
    """The questionnaire answered from the bundle file only (no workspace, no state)."""
    del c
    bundle = read_bundle(path)
    records = bundle.get("records") or {}
    decided = [
        camel(e.get("payload") or {})
        for e in bundle.get("events") or []
        if e.get("eventType") == "human.decision.recorded"
    ]
    answers: dict[str, Any] = {q: None for q in (*QUESTIONS, *EXTRA_QUESTIONS)}
    if not decided:
        return answers
    last = decided[-1]
    actor = last.get("actor") or {}
    digest = last.get("changeSetDigest")
    answers["actor"] = actor.get("actorId")
    answers["digest"] = digest
    answers["time"] = last.get("decidedAt")
    answers["rationale"] = last.get("rationale")
    # Absent means none (domain/models.py: "left out when empty"); the record is the decision.
    answers["acknowledgedRisks"] = sorted(last.get("acknowledgedRisks") or [])
    answers["expiresAt"] = last.get("expiresAt")
    answers["decision"] = last.get("decision")
    everything = dicts(records)
    change_sets = [
        d
        for d in records.get("change_set") or []
        if isinstance(d, dict) and d.get("digest") == digest
    ]
    providers = sorted(
        {
            str(d.get("provider"))
            for d in everything
            if d.get("invocationId")
            and d.get("provider")
            and d.get("callKind") in (None, "implement")
        }
    )
    if change_sets:
        files = sorted(
            str(f.get("path"))
            for f in change_sets[0]["files"]
            if isinstance(f, dict) and f.get("path")
        )
        answers["whoChangedWhat"] = {"files": files, "producers": providers}
        answers["baseRevision"] = change_sets[0].get("baselineRevision")
    snapshot_refs = {
        d.get("configurationSnapshotRef") for d in everything if d.get("configurationSnapshotRef")
    }
    markers = sorted(
        {m for ref in snapshot_refs if (m := find_marker(artifact(bundle, ref))) is not None}
    )
    if markers:
        answers["configurationVersion"] = markers
    validations = [
        {"validatorId": d.get("validatorId"), "status": d.get("status"), "kind": d.get("kind")}
        for d in everything
        if d.get("validatorId")
        and d.get("status")
        and d.get("changeSetDigest") == digest
        and "validationResultId" in d
    ]
    if validations:
        answers["validators"] = sorted(validations, key=lambda v: str(v["validatorId"]))
    # E1-E3: the closure commit as the bundle records it (delivery.commit.created: commit,
    # parent, branch and trailers; an author or a whole message field would be read here).
    created = [
        camel(e.get("payload") or {})
        for e in bundle.get("events") or []
        if e.get("eventType") == "delivery.commit.created"
    ]
    if created:
        answers["closureCommit"] = created[-1].get("commit")
        answers["commitAuthor"] = created[-1].get("author")
        answers["commitMessage"] = created[-1].get("message")
        answers["baseRevision"] = created[-1].get("parent") or answers["baseRevision"]
    answers["recordTypes"] = sorted(records)
    return answers


# ----- D*: the same change in Git and a CI log --------------------------------------------------
def run_dstar(c: dict[str, Any], args: argparse.Namespace, run_dir: Path) -> dict[str, Any]:
    n = c["n"]
    repo = run_dir / "dstar"
    repo.mkdir()
    sample_project(repo)
    git_as(repo, "branch", "-M", "main")
    pyproject = repo / "pyproject.toml"
    pyproject.write_bytes(
        (pyproject.read_text(encoding="utf-8") + f"\n[tool.sample]\nconfigVersion = {n}\n").encode()
    )
    git_as(repo, "commit", "-qam", f"config version {n}")
    base = head(repo)
    git_as(repo, "checkout", "-q", "-b", f"change/{n:02d}")
    files = feature_files(n, pickle=bool(c.get("pickle")), risk=bool(c.get("risk")))
    for relative, content in files.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_bytes(content.encode("utf-8"))
    git_as(repo, "add", "-A")
    title = f"Feature {n:02d} value"
    git_as(repo, "commit", "-qm", title, author=("agent.fixture", "agent.fixture@example.invalid"))
    change = head(repo)
    passed, log = independent_pytest(repo, args)
    ci_log = run_dir / "ci-log.txt"
    ci_log.write_bytes(log.encode("utf-8"))
    git_as(repo, "checkout", "-q", "main")
    started = finished = None
    if c["kind"] != "rejected":
        from audit_support import now_iso

        started = now_iso()
        git_as(
            repo,
            "merge",
            "-q",
            "--no-ff",
            "--no-edit",
            f"change/{n:02d}",
            committer=email_person(c["person"]),
        )
        finished = now_iso()
    truth = {
        "actor": c["person"],
        "digest": change,
        "window": [started, finished] if started else None,
        "rationale": rationale_of(c),
        "acknowledgedRisks": ["newDependency"] if c.get("risk") else [],
        "files": sorted(files),
        "producer": PRODUCER["D*"],
        "marker": n,
        "testsPassed": passed,
        "commitAuthor": "agent.fixture <agent.fixture@example.invalid>",
        "commitMessage": title,
        "baseRevision": base,
    }
    return {
        "reachedDecision": True,
        "truth": truth,
        "answers": answer_from_git(repo, ci_log, f"change/{n:02d}"),
    }


def answer_from_git(repo: Path, ci_log: Path, branch: str) -> dict[str, Any]:
    """The questionnaire answered with ``git`` commands and the CI log only."""
    answers: dict[str, Any] = {q: None for q in (*QUESTIONS, *EXTRA_QUESTIONS)}
    change = git_as(repo, "rev-parse", branch).strip()
    merges = git_as(repo, "log", "main", "--merges", "--format=%H%x1f%P%x1f%cn%x1f%cI%x1f%B%x1e")
    for entry in merges.split("\x1e"):
        parts = entry.strip().split("\x1f")
        if len(parts) < 5:
            continue
        parents = parts[1].split()
        if len(parents) == 2 and parents[1] == change:
            answers["actor"] = parts[2]
            answers["digest"] = parents[1]
            answers["time"] = parts[3]
            # The default merge message records no rationale and no acknowledged risk.
            break
    shown = git_as(repo, "show", "--name-only", "--format=%an%x1f%ae%x1f%P%x1f%B%x1e", change)
    meta, _, names = shown.partition("\x1e")
    fields = meta.split("\x1f")
    author = f"{fields[0]} <{fields[1]}>" if len(fields) >= 2 else None
    files = sorted(line.strip() for line in names.splitlines() if line.strip())
    answers["whoChangedWhat"] = {"files": files, "producers": [fields[0]] if fields else []}
    answers["commitAuthor"] = author
    answers["commitMessage"] = fields[3].strip() if len(fields) >= 4 else None
    answers["baseRevision"] = fields[2].split()[0] if len(fields) >= 3 and fields[2] else None
    config = git_as(repo, "show", f"{change}:pyproject.toml", check=False)
    match = re.search(r"configVersion\s*=\s*(\d+)", config)
    if match:
        answers["configurationVersion"] = [int(match.group(1))]
    log = ci_log.read_text(encoding="utf-8")
    if re.search(r"\b\d+ failed\b", log):
        answers["validators"] = [{"validatorId": "ci.pytest", "status": "FAILED"}]
    elif re.search(r"\b\d+ passed\b", log):
        answers["validators"] = [{"validatorId": "ci.pytest", "status": "PASSED"}]
    return answers


# ----- scoring ---------------------------------------------------------------------------------
def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def score(condition: str, answers: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    """Answerable and exact, per question (``PREREGISTRO.md``, H13)."""
    out: dict[str, Any] = {}
    for question in QUESTIONS:
        value = answers.get(question)
        answerable = value is not None
        exact = False
        if question == "actor":
            exact = value == truth["actor"]
        elif question == "digest":
            exact = value == truth.get("digest")
        elif question == "time":
            moment, window = _instant(value), truth.get("window")
            if moment and window and window[0]:
                low, high = _instant(window[0]), _instant(window[1])
                exact = bool(low and high and low - TIME_SLACK <= moment <= high + TIME_SLACK)
        elif question == "rationale":
            exact = value == truth["rationale"]
        elif question == "acknowledgedRisks":
            exact = answerable and sorted(value) == sorted(truth["acknowledgedRisks"])
        elif question == "whoChangedWhat":
            exact = (
                answerable
                and value.get("files") == truth["files"]
                and truth["producer"] in (value.get("producers") or [])
            )
        elif question == "configurationVersion":
            exact = value == [truth["marker"]]
        elif question == "validators":
            # The validator that runs the project's test command: python.pytest (the Python
            # profile) in H, the CI log's pytest in D*.
            tests = [v for v in value or [] if str(v.get("validatorId")).endswith(".pytest")]
            exact = bool(tests) and all(
                (str(v.get("status")) == "PASSED") == bool(truth.get("testsPassed")) for v in tests
            )
        out[question] = {"answerable": answerable, "exact": answerable and bool(exact)}
    extra: dict[str, Any] = {}
    for question in EXTRA_QUESTIONS:
        if question not in truth:
            extra[question] = {"applicable": False}
            continue
        value = answers.get(question)
        extra[question] = {
            "applicable": True,
            "answerable": value is not None,
            "exact": value is not None and str(value).strip() == str(truth[question]).strip(),
        }
    return {
        "questions": out,
        "answerable": sum(1 for q in out.values() if q["answerable"]),
        "exact": sum(1 for q in out.values() if q["exact"]),
        "extra": extra,
    }


def runner(c: dict[str, Any], condition: str, args: argparse.Namespace, rep: int) -> dict[str, Any]:
    del rep
    run_dir = stamp_dir(args, "h13", c["id"], condition)
    record: dict[str, Any] = {"kind": c["kind"], "person": c["person"], "n": c["n"]}
    record["flags"] = {k: bool(c.get(k)) for k in ("risk", "untraced", "pickle")}
    body = run_h(c, args, run_dir) if condition == "H" else run_dstar(c, args, run_dir)
    record.update(body)
    if record.get("reachedDecision") and "answers" in record:
        record["score"] = score(condition, record["answers"], record["truth"])
        record["brief"] = f"{record['score']['exact']}/8 exact"
    else:
        record["brief"] = "not decided"
    cleanup(run_dir, args.keep_work)
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r]
    summary: dict[str, Any] = {
        "suite": "H13",
        "title": "reconstruct who approved which digest without the workspace",
        "simulatedPerson": "human.ana, human.ben and human.cora decide (a declared simulation); "
        "human.reviewer-simulated answers the waits before DECISION",
        "dstar": "D* is a model of a Git history and a CI log, not an agent",
        "byCondition": {},
    }
    for condition in ("H", "D*"):
        rows = [r for r in ok if r["condition"] == condition]
        scored = [r for r in rows if "score" in r]
        pairs = len(scored) * len(QUESTIONS)
        per_question = {
            q: {
                "answerable": proportion(
                    sum(1 for r in scored if r["score"]["questions"][q]["answerable"]),
                    len(scored),
                ),
                "exact": proportion(
                    sum(1 for r in scored if r["score"]["questions"][q]["exact"]), len(scored)
                ),
            }
            for q in QUESTIONS
        }
        by_kind = {
            kind: {
                "runs": len([r for r in scored if r["kind"] == kind]),
                "answerableOf8": sorted(
                    r["score"]["answerable"] for r in scored if r["kind"] == kind
                ),
                "exactOf8": sorted(r["score"]["exact"] for r in scored if r["kind"] == kind),
            }
            for kind in ("approved", "rejected", "exception", "expired")
        }
        extra = {
            q: {
                "applicable": sum(1 for r in scored if r["score"]["extra"][q]["applicable"]),
                "answerable": sum(1 for r in scored if r["score"]["extra"][q].get("answerable")),
                "exact": sum(1 for r in scored if r["score"]["extra"][q].get("exact")),
            }
            for q in EXTRA_QUESTIONS
        }
        summary["byCondition"][condition] = {
            "runs": len(rows),
            "scored": len(scored),
            "notDecided": sorted(r["case"] for r in rows if "score" not in r),
            "pairsAnswerable": proportion(sum(r["score"]["answerable"] for r in scored), pairs),
            "pairsExact": proportion(sum(r["score"]["exact"] for r in scored), pairs),
            "runsWith8of8Exact": proportion(
                sum(1 for r in scored if r["score"]["exact"] == len(QUESTIONS)), len(scored)
            ),
            "perQuestion": per_question,
            "byKind": by_kind,
            "favouringDirect": extra,
        }
    h_rows = [r for r in ok if r["condition"] == "H"]
    summary["reportedApart"] = {
        "verifyBundle": {
            r["case"]: [r.get("verifyBundleExitCode"), (r.get("verifyBundle") or {}).get("valid")]
            for r in h_rows
        },
        "verifyApproval": {
            r["case"]: r.get("verifyApprovalExitCode")
            for r in h_rows
            if "verifyApprovalExitCode" in r
        },
        "verifyApprovalShiftedExpired": {
            r["case"]: r.get("verifyApprovalShiftedExitCode")
            for r in h_rows
            if r["kind"] == "expired"
        },
        "verifyBundleShiftedExpired": {
            r["case"]: r.get("verifyBundleShifted") for r in h_rows if r["kind"] == "expired"
        },
        "expiredFlag": {
            r["case"]: [
                d.get("expired") for d in (r.get("verifyBundle") or {}).get("decisions") or []
            ]
            for r in h_rows
            if r["kind"] == "expired"
        },
    }
    h = summary["byCondition"]["H"]
    full = [r for r in h_rows if "score" in r and r["score"]["exact"] == len(QUESTIONS)]
    summary["criteria"] = {
        "h8of8Exact": {
            "value": f"{len(full)}/{len(CASES)} H runs answer 8/8 exactly ({h['scored']} scored)",
            "met": len(full) == len(CASES),
        },
        "dstarReported": summary["byCondition"]["D*"]["pairsExact"],
        "note": "Pre-registered in PREREGISTRO.md (H13, amendment 1); evaluated as is. A run "
        "that did not reach DECISION counts against the criterion.",
    }
    return summary


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H13",
            title="reconstruct who approved which digest without the workspace",
            stem="h13-bundle-audit",
            cases=CASES,
            conditions_of=conditions_of,
            all_conditions=["H", "D*"],
            runner=runner,
            summarize=summarize,
        )
    )
