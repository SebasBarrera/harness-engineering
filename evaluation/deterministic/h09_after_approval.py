#!/usr/bin/env python3
"""H09: a change after the approval, at the gate and in CI (``CATALOGO.md`` §2.1,
``PREREGISTRO.md``). No model.

A governed run (``harness init``, the fixture command provider writing the clean change of H01 to
the inventory library) reaches DECISION with the gate ``PASSED``; the declared simulated person
(``human.reviewer-simulated``) approves it with its digest; the closure commit lands on the branch
``harness/<run>`` (``delivery.closureCommit: branch``) and the run is exported as an evidence
bundle. Then, in a clone of the repository:

* 12 post-approval mutations of the closure branch (one byte, a final newline, a rename, a mode
  change, a new file, an approved file deleted, a later commit to another file, ``--amend``, a
  rebase onto a base that changed the same file, a squash with a later commit, a symbolic link,
  CRLF line endings), 3 integrations without change (merge ``--no-ff``, a rebase onto a base that
  advanced in an unrelated file, merge ``--squash``) and a control (the closure branch as it is);
* ``ci-verify-approval``: ``harness verify-approval --base --head --bundle --no-workspace`` on
  each (5 is the refusal, 0 the pass);
* ``direct-equivalent`` (D*): the same revision merged without any link to an approval;
  "delivered" means the visible tests pass on it.

``harness`` (the gate stage): each of the 9 mutations that can be made in a workspace, in a new
run waiting in DECISION, then ``gate decide APPROVE`` with the evaluated digest (5 expected) and
``run continue`` (``verification.reverify.authorized``); and a control without mutation.

Usage:
  python evaluation/deterministic/h09_after_approval.py --out DIR [--harness PATH] [--work DIR]
      [--project-venv DIR] [--only id,...] [--conditions a,b] [--reps 1] [--summarize-only]

Output: DIR/environment.json, DIR/h09-after-approval.jsonl (one record per case, condition and
repetition), DIR/h09-after-approval-summary.json, DIR/configs/harness.yaml.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import interception  # noqa: E402
from common import (  # noqa: E402
    REPO,
    SIMULATED_PERSON,
    Harness,
    add_common_arguments,
    environment,
    git,
    init_repo,
    query,
    simulated_person,
    stamp,
    state_database,
    write_json,
    write_jsonl,
)
from h01_weakened_tests import FEATURE_TEST, NEW_TEST, SOURCE, source  # noqa: E402
from h01_weakened_tests import build_baseline as h01_baseline  # noqa: E402

DIRECT = interception.DIRECT
GATE = "harness"
CI = "ci-verify-approval"
CONDITIONS = (DIRECT, GATE, CI)
README = "# Inventory\n\nItems and stock movements.\n"
RENAMED = "tests/test_stock_report.py"
EXTRA = "src/inventory/extra.py"
LINK = "docs/link"
CHANGE = {SOURCE: source(), NEW_TEST: FEATURE_TEST}


def build_baseline(root: Path) -> None:
    h01_baseline(root)
    interception.write_text(root / "README.md", README)


# ----- mutations in a workspace (the gate stage) --------------------------------------------
def ws_byte(ws: Path) -> None:
    path = ws / SOURCE
    path.write_bytes(path.read_bytes().replace(b'"] < threshold)', b'"] <= threshold)', 1))


def ws_newline(ws: Path) -> None:
    with (ws / SOURCE).open("ab") as handle:
        handle.write(b"\n")


def ws_rename(ws: Path) -> None:
    (ws / NEW_TEST).rename(ws / RENAMED)


def ws_mode(ws: Path) -> None:
    os.chmod(ws / SOURCE, 0o700)


def ws_new_file(ws: Path) -> None:
    interception.write_text(ws / EXTRA, "LIMIT = 5\n")


def ws_delete(ws: Path) -> None:
    (ws / NEW_TEST).unlink()


def ws_other_file(ws: Path) -> None:
    with (ws / "README.md").open("ab") as handle:
        handle.write(b"\nAdjusted after the review.\n")


def ws_symlink(ws: Path) -> None:
    (ws / LINK).parent.mkdir(parents=True, exist_ok=True)
    (ws / LINK).symlink_to("../README.md")


def ws_crlf(ws: Path) -> None:
    path = ws / SOURCE
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))


# ----- revisions in a clone (the CI stage) --------------------------------------------------
@dataclass
class Clone:
    root: Path
    base: str
    close: str

    def git(self, *args: str) -> str:
        return git(self.root, "-c", "core.autocrlf=false", *args)

    def branch(self, name: str, start: str) -> None:
        self.git("checkout", "-q", "-B", name, start)

    def commit(self, message: str, *extra: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message, *extra)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()


Range = tuple[str, str]


def rev_byte(c: Clone) -> Range:
    c.branch("h09/byte", c.close)
    ws_byte(c.root)
    c.commit("one byte after the approval")
    return c.base, c.head()


def rev_newline(c: Clone) -> Range:
    c.branch("h09/newline", c.close)
    ws_newline(c.root)
    c.commit("final newline")
    return c.base, c.head()


def rev_rename(c: Clone) -> Range:
    c.branch("h09/rename", c.close)
    c.git("mv", NEW_TEST, RENAMED)
    c.commit("rename the test")
    return c.base, c.head()


def rev_mode(c: Clone) -> Range:
    c.branch("h09/mode", c.close)
    c.git("update-index", "--chmod=+x", SOURCE)
    c.git("commit", "-q", "-m", "make the module executable")
    return c.base, c.head()


def rev_new_file(c: Clone) -> Range:
    c.branch("h09/new-file", c.close)
    ws_new_file(c.root)
    c.commit("add a module")
    return c.base, c.head()


def rev_delete(c: Clone) -> Range:
    c.branch("h09/delete", c.close)
    c.git("rm", "-q", NEW_TEST)
    c.git("commit", "-q", "-m", "drop the test")
    return c.base, c.head()


def rev_other_file(c: Clone) -> Range:
    c.branch("h09/other-file", c.close)
    ws_other_file(c.root)
    c.commit("touch the README")
    return c.base, c.head()


def rev_amend(c: Clone) -> Range:
    c.branch("h09/amend", c.close)
    path = c.root / SOURCE
    path.write_bytes(path.read_bytes() + b"# amended after the approval\n")
    c.git("add", "-A")
    c.git("commit", "-q", "--amend", "--no-edit")
    return c.base, c.head()


def rev_rebase_altered(c: Clone) -> Range:
    c.branch("h09/base-same-file", c.base)
    path = c.root / SOURCE
    path.write_bytes(
        path.read_bytes().replace(b'"""Inventory library', b'"""The inventory library', 1)
    )
    c.commit("reword the module docstring")
    new_base = c.head()
    c.branch("h09/rebase-altered", c.close)
    c.git("rebase", "-q", new_base)
    return new_base, c.head()


def rev_squash_extra(c: Clone) -> Range:
    c.branch("h09/squash-extra", c.close)
    path = c.root / SOURCE
    path.write_bytes(path.read_bytes() + b"# follow-up fix\n")
    c.commit("follow-up")
    c.git("reset", "-q", "--soft", c.base)
    c.git("commit", "-q", "-m", "squashed")
    return c.base, c.head()


def rev_symlink(c: Clone) -> Range:
    c.branch("h09/symlink", c.close)
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=c.root,
        input="../README.md",
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout.strip()
    c.git("update-index", "--add", "--cacheinfo", f"120000,{blob},{LINK}")
    c.git("commit", "-q", "-m", "add a link")
    return c.base, c.head()


def rev_crlf(c: Clone) -> Range:
    c.branch("h09/crlf", c.close)
    ws_crlf(c.root)
    c.commit("line endings")
    return c.base, c.head()


def rev_merge(c: Clone) -> Range:
    c.branch("h09/main-merge", c.base)
    c.git("merge", "-q", "--no-ff", "-m", "merge the approved branch", c.close)
    return c.base, c.head()


def rev_rebase_clean(c: Clone) -> Range:
    c.branch("h09/base-unrelated", c.base)
    interception.write_text(c.root / "docs" / "unrelated.md", "Unrelated.\n")
    c.commit("an unrelated change on the base")
    new_base = c.head()
    c.branch("h09/rebase-clean", c.close)
    c.git("rebase", "-q", new_base)
    return new_base, c.head()


def rev_squash_merge(c: Clone) -> Range:
    c.branch("h09/main-squash", c.base)
    c.git("merge", "-q", "--squash", c.close)
    c.git("commit", "-q", "-m", "squash merge of the approved branch")
    return c.base, c.head()


def rev_control(c: Clone) -> Range:
    return c.base, c.close


@dataclass(frozen=True)
class Case:
    id: str
    kind: str  # mutation, integration, control
    description: str
    revision: Callable[[Clone], Range]
    workspace: Callable[[Path], None] | None = None


CASES: tuple[Case, ...] = (
    Case("byte", "mutation", "one byte changes the behaviour (< to <=)", rev_byte, ws_byte),
    Case("newline", "mutation", "a final newline is added", rev_newline, ws_newline),
    Case("rename", "mutation", "the approved test file is renamed", rev_rename, ws_rename),
    Case("mode", "mutation", "the module becomes executable (+x)", rev_mode, ws_mode),
    Case("new-file", "mutation", "a new module is added", rev_new_file, ws_new_file),
    Case("delete", "mutation", "the approved test file is deleted", rev_delete, ws_delete),
    Case("other-file", "mutation", "a later change to the README", rev_other_file, ws_other_file),
    Case("amend", "mutation", "the closure commit is amended", rev_amend),
    Case(
        "rebase-altered",
        "mutation",
        "a rebase onto a base that changed the same file",
        rev_rebase_altered,
    ),
    Case("squash-extra", "mutation", "a squash with a later commit", rev_squash_extra),
    Case("symlink", "mutation", "a symbolic link is added", rev_symlink, ws_symlink),
    Case("crlf", "mutation", "the module gets CRLF line endings", rev_crlf, ws_crlf),
    Case("merge", "integration", "merge --no-ff of the approved branch", rev_merge),
    Case(
        "rebase-clean",
        "integration",
        "a rebase onto a base that advanced in an unrelated file",
        rev_rebase_clean,
    ),
    Case("squash-merge", "integration", "merge --squash of the approved branch", rev_squash_merge),
    Case("control", "control", "the closure branch as it is", rev_control),
)
GATE_CONTROL = "gate-control"


# ----- a governed run waiting in DECISION ----------------------------------------------------
@dataclass
class Waiting:
    h: Harness
    ws: Path
    run_dir: Path
    run_id: str
    digest: str
    status: dict[str, Any]
    deviations: list[str]

    @property
    def at_decision(self) -> bool:
        execution = self.status.get("execution") or {}
        gate = self.status.get("gate") or {}
        return execution.get("currentPhase") == "DECISION" and gate.get("status") == "PASSED"


def to_decision(args: argparse.Namespace, label: str) -> Waiting:
    run_dir = args.work / f"H09-{label}-{stamp()}"
    ws = run_dir / "ws"
    ws.mkdir(parents=True)
    build_baseline(ws)
    init_repo(ws)
    h = Harness(
        args.harness, interception.harness_env(args.project_venv, run_dir / "harness-state")
    )
    h.run(ws, "init", "--path", ".", label="init")
    if git(ws, "status", "--porcelain").strip():
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "harness init")
    config_path = ws / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    change = run_dir / "change.json"
    interception.write_text(change, json.dumps({"write": CHANGE, "delete": []}))
    deviations = interception.point_at_fixture(config, change, core=False)
    interception.write_text(config_path, yaml.safe_dump(config, sort_keys=False))
    saved = args.out / "configs" / f"{GATE}.yaml"
    if not saved.exists():
        text = yaml.safe_dump(config, sort_keys=False)
        for local, placeholder in ((str(run_dir), "<run>"), (str(REPO), "<repo>")):
            text = text.replace(local, placeholder)
        interception.write_text(saved, text)
    task = {
        "taskId": "task_h09_" + label.replace("-", "_"),
        "title": "Low stock report",
        "intent": "Add Inventory.low_stock(threshold), which reports the SKUs with low stock.",
        "acceptanceCriteria": [
            {
                "criterionId": "ac_low_stock",
                "text": "low_stock(threshold) returns the sorted SKUs whose quantity is below the threshold.",
            }
        ],
    }
    task_path = run_dir / "task.yaml"
    interception.write_text(task_path, yaml.safe_dump(task, sort_keys=False))
    h.run(ws, "task", "create", "--path", ".", "--file", str(task_path), label="task create")
    code, started, _ = h.run(
        ws, "run", "start", "--path", ".", "--task", task["taskId"], label="run start"
    )
    run_id = started.get("executionId", "") if isinstance(started, dict) else ""
    if not run_id:
        _, latest, _ = h.run(ws, "status", "--path", ".", label="status latest")
        run_id = (
            ((latest.get("execution") or {}).get("executionId", ""))
            if isinstance(latest, dict)
            else ""
        )
    simulated_person(h, ws, run_id, task["taskId"], run_dir, [], code)
    status = current(h, ws, run_id)
    digest = str((status.get("execution") or {}).get("changeSetDigest") or "")
    return Waiting(h, ws, run_dir, run_id, digest, status, deviations)


def current(h: Harness, ws: Path, run_id: str) -> dict[str, Any]:
    _, status, _ = h.run(ws, "status", "--path", ".", "--run", run_id, label="status")
    return status if isinstance(status, dict) else {}


def approve(w: Waiting, digest: str) -> tuple[int, str | None]:
    code, payload, _ = w.h.run(
        w.ws,
        "gate",
        "decide",
        "--path",
        ".",
        "--run",
        w.run_id,
        "--decision",
        "APPROVE",
        "--change-set-digest",
        digest,
        "--actor",
        SIMULATED_PERSON,
        "--rationale",
        "simulated approval of the evaluated ChangeSet",
        label="gate decide APPROVE",
    )
    error = payload.get("error") if isinstance(payload, dict) else None
    return code, (str(error)[-400:] if error else None)


def event_types(w: Waiting) -> list[str]:
    database = state_database(w.h, w.ws)
    if not database.exists():
        return []
    rows = query(
        database,
        "select event_type from events where execution_id = ? order by execution_sequence",
        (w.run_id,),
    )
    return [row[0] for row in rows]


def waiting_summary(w: Waiting) -> dict[str, Any]:
    execution = w.status.get("execution") or {}
    return {
        "runId": w.run_id,
        "phase": execution.get("currentPhase"),
        "runStatus": execution.get("status"),
        "gate": (w.status.get("gate") or {}).get("status"),
        "gateReasons": (w.status.get("gate") or {}).get("reasonCodes", []),
        "deviations": w.deviations,
    }


# ----- the gate stage ------------------------------------------------------------------------
def gate_case(case: Case | None, args: argparse.Namespace, rep: int) -> dict[str, Any]:
    label = f"gate-{case.id if case else 'control'}-r{rep}"
    w = to_decision(args, label)
    record: dict[str, Any] = {"setup": waiting_summary(w), "applicable": True}
    if not w.at_decision:
        record["apparatusError"] = "the run did not reach DECISION with the gate PASSED"
        record["steps"] = w.h.steps
        return record
    if case is not None and case.workspace is not None:
        case.workspace(w.ws)
    code, error = approve(w, w.digest)
    record.update({"approveExitCode": code, "approveError": error})
    if code != 0:
        continued, _, _ = w.h.run(
            w.ws, "run", "continue", "--path", ".", "--run", w.run_id, label="run continue"
        )
        after = current(w.h, w.ws, w.run_id)
        execution = after.get("execution") or {}
        record["afterContinue"] = {
            "exitCode": continued,
            "phase": execution.get("currentPhase"),
            "runStatus": execution.get("status"),
            "gate": (after.get("gate") or {}).get("status"),
            "digestChanged": execution.get("changeSetDigest") != w.digest,
        }
    types = event_types(w)
    record["reverifyAuthorized"] = "verification.reverify.authorized" in types
    record["rejected"] = code == 5
    record["steps"] = w.h.steps
    return record


# ----- the CI stage and D* --------------------------------------------------------------------
def approved_clone(args: argparse.Namespace, rep: int) -> tuple[Clone | None, dict[str, Any]]:
    w = to_decision(args, f"ci-r{rep}")
    setup: dict[str, Any] = {"setup": waiting_summary(w)}
    if not w.at_decision:
        setup["apparatusError"] = "the run did not reach DECISION with the gate PASSED"
        return None, setup
    code, error = approve(w, w.digest)
    setup.update({"approveExitCode": code, "approveError": error})
    branch = f"harness/{w.run_id}"
    try:
        close = git(w.ws, "rev-parse", f"refs/heads/{branch}").strip()
        base = git(w.ws, "rev-parse", f"{close}~1").strip()
    except subprocess.CalledProcessError:
        setup["apparatusError"] = f"no closure branch {branch} after the approval"
        setup["eventTypes"] = event_types(w)
        return None, setup
    bundle = w.run_dir / "evidence.tar.gz"
    exported, _, _ = w.h.run(
        w.ws, "export", "--path", ".", "--run", w.run_id, "--bundle", str(bundle), label="export"
    )
    setup.update({"exportExitCode": exported, "approvedDigest": w.digest})
    if exported != 0 or not bundle.is_file():
        setup["apparatusError"] = "the evidence bundle could not be exported"
        return None, setup
    clone_root = w.run_dir / "clone"
    git(w.run_dir, "clone", "-q", str(w.ws), str(clone_root))
    clone = Clone(clone_root, base, close)
    setup["bundle"] = bundle
    setup["h"] = w.h
    return clone, setup


def verify(h: Harness, clone: Clone, bundle: Path, rng: Range) -> dict[str, Any]:
    code, report, _ = h.run(
        clone.root,
        "verify-approval",
        "--path",
        ".",
        "--base",
        rng[0],
        "--head",
        rng[1],
        "--bundle",
        str(bundle),
        "--no-workspace",
        label="verify-approval",
    )
    report = report if isinstance(report, dict) else {}
    return {
        "exitCode": code,
        "status": report.get("status"),
        "computedDigest": report.get("computedDigest"),
        "special": report.get("special"),
        "files": [f"{f.get('status')}:{f.get('path')}" for f in report.get("files") or []],
        "reasons": report.get("reasons"),
    }


def tests_pass(args: argparse.Namespace, clone: Clone, head: str) -> dict[str, Any]:
    clone.git("checkout", "-q", "--detach", head)
    proc = subprocess.run(
        [
            str(interception.venv_python(args.project_venv)),
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=clone.root,
        env=interception.harness_env(args.project_venv, clone.root.parent / "direct-state"),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {"testsExitCode": proc.returncode, "delivered": proc.returncode == 0}


def base_record(
    case_id: str, kind: str, description: str, condition: str, rep: int
) -> dict[str, Any]:
    return {
        "suite": "H09",
        "case": case_id,
        "kind": kind,
        "description": description,
        "condition": condition,
        "rep": rep,
        "modelCalls": 0,
    }


def run_rep(
    cases: list[Case], wanted: list[str], args: argparse.Namespace, rep: int
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if DIRECT in wanted or CI in wanted:
        try:
            clone, setup = approved_clone(args, rep)
        except Exception:  # noqa: BLE001 - recorded, not hidden
            clone, setup = None, {"apparatusError": traceback.format_exc()[-2000:]}
        h = setup.pop("h", None)
        bundle = setup.pop("bundle", None)
        ranges: dict[str, Range] = {}
        for case in cases:
            for condition in (c for c in (DIRECT, CI) if c in wanted):
                record = base_record(case.id, case.kind, case.description, condition, rep)
                record["ciSetup"] = setup
                if clone is None:
                    record["apparatusError"] = setup.get("apparatusError")
                    records.append(record)
                    continue
                try:
                    if case.id not in ranges:
                        ranges[case.id] = case.revision(clone)
                    rng = ranges[case.id]
                    record["range"] = list(rng)
                    if condition == CI:
                        assert h is not None and bundle is not None
                        record["verify"] = verify(h, clone, bundle, rng)
                        record["rejected"] = record["verify"]["exitCode"] == 5
                    else:
                        record.update(tests_pass(args, clone, rng[1]))
                except Exception:  # noqa: BLE001 - recorded, not hidden
                    record["apparatusError"] = traceback.format_exc()[-2000:]
                    # Leave the clone usable for the next case.
                    for cleanup in (("rebase", "--abort"), ("reset", "-q", "--hard")):
                        with contextlib.suppress(subprocess.CalledProcessError):
                            clone.git(*cleanup)
                records.append(echo(record))
    if GATE in wanted:
        gate_cases: list[Case | None] = [c for c in cases if c.kind == "mutation"]
        if not args.only or GATE_CONTROL in args.only.split(","):
            gate_cases.append(None)
        for item in gate_cases:
            if item is None:
                record = base_record(
                    GATE_CONTROL, "control", "no change before the approval", GATE, rep
                )
            else:
                record = base_record(item.id, item.kind, item.description, GATE, rep)
            if item is not None and item.workspace is None:
                record.update({"applicable": False, "reason": "a Git-only operation"})
                records.append(echo(record))
                continue
            try:
                record.update(gate_case(item, args, rep))
            except Exception:  # noqa: BLE001 - recorded, not hidden
                record["apparatusError"] = traceback.format_exc()[-2000:]
            records.append(echo(record))
    return records


def echo(record: dict[str, Any]) -> dict[str, Any]:
    print(
        "H09",
        record["case"],
        record["condition"],
        record["rep"],
        "ERROR" if record.get("apparatusError") else "",
        record.get("delivered", ""),
        (record.get("verify") or {}).get("exitCode", ""),
        record.get("approveExitCode", ""),
        flush=True,
    )
    return record


# ----- summary ---------------------------------------------------------------------------------
def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    first = [r for r in records if r["rep"] == 1 and not r.get("apparatusError")]

    def rows(condition: str, kind: str) -> list[dict[str, Any]]:
        return [
            r
            for r in first
            if r["condition"] == condition and r["kind"] == kind and r.get("applicable", True)
        ]

    ci_mut, ci_int = rows(CI, "mutation"), rows(CI, "integration")
    gate_mut = rows(GATE, "mutation")
    gate_control = [r for r in rows(GATE, "control")]
    by: dict[str, Any] = {
        CI: {
            "mutationsRejected": interception.proportion(
                sum(r["rejected"] for r in ci_mut), len(ci_mut)
            ),
            "integrationsRejected": interception.proportion(
                sum(r["rejected"] for r in ci_int), len(ci_int)
            ),
            "exitCodes": {
                r["case"]: r["verify"]["exitCode"]
                for r in rows(CI, "mutation") + ci_int + rows(CI, "control")
            },
            "notRejected": sorted(r["case"] for r in ci_mut if not r["rejected"]),
            "falselyRejected": sorted(r["case"] for r in ci_int if r["rejected"]),
        },
        GATE: {
            "mutationsRejected": interception.proportion(
                sum(r["rejected"] for r in gate_mut), len(gate_mut)
            ),
            "notApplicable": sorted(
                r["case"] for r in first if r["condition"] == GATE and not r.get("applicable", True)
            ),
            "exitCodes": {r["case"]: r.get("approveExitCode") for r in gate_mut + gate_control},
            "reverifyAuthorized": interception.proportion(
                sum(bool(r.get("reverifyAuthorized")) for r in gate_mut), len(gate_mut)
            ),
            "gateAfterContinue": {
                r["case"]: (r.get("afterContinue") or {}).get("gate") for r in gate_mut
            },
            "controlAccepted": [r.get("approveExitCode") == 0 for r in gate_control],
            "notRejected": sorted(r["case"] for r in gate_mut if not r["rejected"]),
        },
        DIRECT: {
            kind: interception.proportion(
                sum(r["delivered"] for r in rows(DIRECT, kind)), len(rows(DIRECT, kind))
            )
            for kind in ("mutation", "integration", "control")
        },
    }
    signatures: dict[tuple[str, str], set[str]] = {}
    for r in records:
        if r.get("apparatusError"):
            continue
        signature = json.dumps(
            [r.get("delivered"), (r.get("verify") or {}).get("exitCode"), r.get("approveExitCode")]
        )
        signatures.setdefault((r["case"], r["condition"]), set()).add(signature)
    return {
        "suite": "H09",
        "title": "a change after the approval: digest, gate and verify-approval in CI",
        "directEquivalent": "D* merges the revision without any link to an approval; delivered means the visible tests pass on it (a model of a direct agent, not an agent, CATALOGO.md §4.4)",
        "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION and approves the evaluated ChangeSet (a declared simulation)",
        "cases": {
            kind: sum(1 for c in CASES if c.kind == kind)
            for kind in ("mutation", "integration", "control")
        },
        "gateApplicable": sorted(c.id for c in CASES if c.workspace is not None),
        "records": len(records),
        "apparatusErrors": sorted(
            f"{r['case']}/{r['condition']}/r{r['rep']}" for r in records if r.get("apparatusError")
        ),
        "nonIdenticalRepetitions": sorted(
            f"{case}/{condition}" for (case, condition), v in signatures.items() if len(v) > 1
        ),
        "byCondition": by,
        "notes": {
            "ci": "harness verify-approval --base --head --bundle --no-workspace in a clone of the repository",
            "gate": "a new run waiting in DECISION per mutation; APPROVE with the evaluated digest, then run continue",
            "forge": "rebase and squash are reproduced with local Git; no forge integration was tested",
        },
    }


def criteria(summary: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered criterion (PREREGISTRO.md, H09), evaluated as it is."""
    by = summary["byCondition"]
    ci_mut = by[CI]["mutationsRejected"]
    ci_int = by[CI]["integrationsRejected"]
    gate_mut = by[GATE]["mutationsRejected"]
    control = by[GATE]["controlAccepted"]
    return {
        "statement": "verify-approval rejects 12/12 mutations (exit 5) with 0 false rejections in the 3 integrations; at the gate the 9 applicable mutations are rejected (exit 5) and the control is accepted",
        "ciMutationsRejected": ci_mut,
        "ciIntegrationsRejected": ci_int,
        "gateMutationsRejected": gate_mut,
        "gateControlAccepted": control,
        "met": bool(
            ci_mut["n"] == 12
            and ci_mut["k"] == 12
            and ci_int["n"] == 3
            and ci_int["k"] == 0
            and gate_mut["n"] == 9
            and gate_mut["k"] == 9
            and control == [True]
        ),
        "evaluable": ci_mut["n"] == 12
        and ci_int["n"] == 3
        and gate_mut["n"] == 9
        and len(control) == 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument(
        "--project-venv",
        type=Path,
        default=Path(sys.executable).parent.parent,
        help="venv whose python runs the project's tests (pytest)",
    )
    parser.add_argument("--only", default="", help="comma-separated case ids (gate-control too)")
    parser.add_argument("--conditions", default="", help="comma-separated condition names")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.work = (args.work or args.out / ".work").resolve()
    args.project_venv = args.project_venv.resolve()
    if not shutil.which(args.harness):
        parser.error(f"harness executable not found: {args.harness}")
    args.work.mkdir(parents=True, exist_ok=True)
    records_path = args.out / "h09-after-approval.jsonl"
    if not args.summarize_only:
        wanted = [c for c in args.conditions.split(",") if c] or list(CONDITIONS)
        unknown = sorted(set(wanted) - set(CONDITIONS))
        if unknown:
            parser.error(f"unknown conditions: {', '.join(unknown)}")
        ids = {c for c in args.only.split(",") if c}
        cases = [c for c in CASES if not ids or c.id in ids]
        write_json(
            args.out / "environment.json",
            environment(
                args.harness,
                args.wheel,
                {
                    "suite": "H09 a change after the approval",
                    "conditions": wanted,
                    "cases": [c.id for c in cases],
                    "reps": args.reps,
                    "projectVenv": args.project_venv.name,
                    "platformDeviations": (
                        ["runtime.agentSandbox: off (no write sandbox on Windows)"]
                        if interception.WINDOWS
                        else []
                    ),
                },
            ),
        )
        records = [r for rep in range(1, args.reps + 1) for r in run_rep(cases, wanted, args, rep)]
        write_jsonl(records_path, records)
    text = records_path.read_text(encoding="utf-8")
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    summary = summarize(records)
    summary["criteria"] = criteria(summary)
    write_json(args.out / "h09-after-approval-summary.json", summary)
    return 1 if summary["apparatusErrors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
