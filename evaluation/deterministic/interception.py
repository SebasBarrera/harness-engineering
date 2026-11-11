"""Shared engine of the interception suites H01, H03 and H05 (``CATALOGO.md`` §2.1). No model.

A suite is a baseline project, a corpus of cases (each one change, seeded or clean, given as whole
files to write and paths to delete) and a set of conditions. Every (case, condition) runs on a
fresh copy of the baseline:

* ``direct-equivalent`` (D*): the change applied to the copy and the project's test command
  (``python -m pytest -q``); "delivered" means the visible tests pass. D* is a model of a direct
  agent that delivers what the change contains; it is not an agent (``CATALOGO.md`` §4.4);
* a governed condition: ``harness init``, the condition's configuration, the fixture command
  provider ``interception_agent.py`` (it writes exactly the seeded change; why not the simulated
  provider's patch mode is in ``PREREGISTRO.md``), ``task create``, ``run start``, the declared
  simulated person for the waits before DECISION (``common.simulated_person``), then ``status``
  and ``findings list``. No decision is taken unless the suite's ``after_decision`` hook says so.

Each record holds what the pre-registration asks for: delivered by D*, the run's status, phase
and gate, every finding (rule, severity, path, line), whether it blocks, and the suite's
detection verdict. The summary computes, per condition, sensitivity per group and rule,
specificity per rule on the clean cases, and the false positives on clean cases, with Wilson
intervals. The suite exits 0 when it ran, whatever the numbers; 1 only when its own apparatus
failed on some case (the record says where).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import (  # noqa: E402
    GIT_ENV,
    HERE,
    REPO,
    Harness,
    add_common_arguments,
    environment,
    git,
    init_repo,
    isolated_env,
    simulated_person,
    stamp,
    state_database,
    write_json,
    write_jsonl,
)
from conditions import core_config, grant_provider_command  # noqa: E402

AGENT = HERE / "interception_agent.py"
DIRECT = "direct-equivalent"
BLOCK_SEVERITIES = ("HIGH", "CRITICAL")
WINDOWS = sys.platform == "win32"
# Passed through on Windows only: a Python process there needs SYSTEMROOT, and Git and the
# system tools live in the caller's PATH (the POSIX environment of common.isolated_env is fixed).
_WINDOWS_PASSTHROUGH = (
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PYTHONPATH",
)

ConfigBuilder = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class Suite:
    """One interception suite: its id, baseline, cases and conditions."""

    suite_id: str
    title: str
    build_baseline: Callable[[Path], None]
    cases: list[dict[str, Any]]
    conditions: dict[str, ConfigBuilder | None]
    """Condition name -> configuration builder over the ``harness init`` file (``None`` for
    D*). A builder that returns the H-core file ignores its input."""
    task_for: Callable[[dict[str, Any], str], dict[str, Any]]
    detect: Callable[[dict[str, Any], list[dict[str, Any]]], bool]
    """Whether the condition detected the case, from its findings (the suite's definition)."""
    rules: tuple[str, ...]
    """The rules whose specificity is computed on the clean cases."""
    relevant: tuple[str, ...]
    """Rule-id prefixes recorded apart as the suite's own findings."""
    after_decision: (
        Callable[[Harness, Path, str, dict[str, Any], dict[str, Any]], dict[str, Any]] | None
    ) = None
    notes: dict[str, Any] = field(default_factory=dict)
    after_direct: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None
    """D*'s counterpart of ``after_decision``: reads the workspace D* delivered (H15)."""


# ----- environment ------------------------------------------------------------------------------
def venv_bin(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def venv_python(venv: Path) -> Path:
    return venv_bin(venv) / ("python.exe" if os.name == "nt" else "python")


def harness_env(venv: Path, state_root: Path) -> dict[str, str]:
    """``common.isolated_env`` on POSIX; on Windows the same keys with the platform's separator
    and the variables a Windows process needs."""
    if not WINDOWS:
        return isolated_env([venv_bin(venv)], state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "anchors").mkdir(exist_ok=True)
    (state_root / "state").mkdir(exist_ok=True)
    env = {name: os.environ[name] for name in _WINDOWS_PASSTHROUGH if name in os.environ}
    env.update(
        {
            "PATH": os.pathsep.join([str(venv_bin(venv)), os.environ.get("PATH", "")]),
            "HOME": os.environ.get("HOME") or os.environ.get("USERPROFILE", ""),
            "LANG": "en_US.UTF-8",
            "PYTHONUTF8": "1",
            "HARNESS_ANCHOR_DIR": str(state_root / "anchors"),
            "HARNESS_STATE_DIR": str(state_root / "state"),
            **GIT_ENV,
        }
    )
    return env


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 with LF line endings on every platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def write_files(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        write_text(root / relative, content)


def apply_change(root: Path, case: dict[str, Any]) -> None:
    write_files(root, case.get("write") or {})
    for relative in case.get("delete") or []:
        (root / relative).unlink(missing_ok=True)
    for relative, text in (case.get("append") or {}).items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        with (root / relative).open("ab") as handle:
            handle.write(text.encode("utf-8"))
    for relative, target in (case.get("symlink") or {}).items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).symlink_to(target)


def change_document(case: dict[str, Any]) -> dict[str, Any]:
    """The change file the fixture agent reads (``interception_agent.py``)."""
    document: dict[str, Any] = {
        "write": case.get("write") or {},
        "delete": case.get("delete") or [],
    }
    # "results", "summary" and "selfReport" (H06, H07, H12): what the fixture agent answers.
    for key in ("append", "symlink", "results", "summary", "selfReport"):
        if case.get(key):
            document[key] = case[key]
    if case.get("tolerateDenied"):
        document["tolerateDenied"] = True
    return document


def wilson(successes: int, total: int, z: float = 1.959964) -> list[float] | None:
    """The 95 % Wilson score interval of a proportion (``CATALOGO.md`` §4.5)."""
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def proportion(successes: int, total: int) -> dict[str, Any]:
    return {
        "k": successes,
        "n": total,
        "rate": round(successes / total, 3) if total else None,
        "wilson95": wilson(successes, total),
    }


# ----- configurations ---------------------------------------------------------------------------
def init_config(config: dict[str, Any]) -> dict[str, Any]:
    """H: the file ``harness init`` wrote, as it is."""
    return config


def core_condition(config: dict[str, Any]) -> dict[str, Any]:
    """H-core: the 1.0.0 key set (``evaluation/conditions.py``)."""
    return core_config(str(config.get("projectId") or "project_ws"))


def provider_command(change: Path) -> list[str]:
    return ["python", str(AGENT), "--change", str(change)]


def point_at_fixture(config: dict[str, Any], change: Path, *, core: bool) -> list[str]:
    """The fixture provider and the grant of exactly its command line; on Windows the agent
    sandbox (macOS and Linux only) is turned off and the deviation returned."""
    command = provider_command(change)
    config["agentProvider"] = "fixture"
    config["agentProviders"] = {
        "fixture": {"kind": "command", "command": command, "model": "deterministic-fixture"}
    }
    grant_provider_command(config, command, core=core)
    deviations: list[str] = []
    runtime = config.setdefault("runtime", {})
    if WINDOWS and runtime.get("agentSandbox") not in (None, "off"):
        runtime["agentSandbox"] = "off"
        deviations.append("runtime.agentSandbox: off (no write sandbox on Windows)")
    return deviations


# ----- one case ---------------------------------------------------------------------------------
def direct_equivalent(
    suite: Suite, case: dict[str, Any], args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    run_dir = args.work / f"{suite.suite_id}-{case['id']}-direct-r{rep}-{stamp()}"
    ws = run_dir / "ws"
    ws.mkdir(parents=True)
    suite.build_baseline(ws)
    apply_change(ws, case)
    env = harness_env(args.project_venv, run_dir / "state")
    try:
        proc = subprocess.run(
            [str(venv_python(args.project_venv)), "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=ws,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        code, tail = proc.returncode, (proc.stdout + proc.stderr)[-600:]
    except subprocess.TimeoutExpired:
        code, tail = -9, "TIMEOUT"
    summary = next(
        (line for line in reversed(tail.splitlines()) if " passed" in line or " failed" in line),
        "",
    )
    record = {"testsExitCode": code, "delivered": code == 0, "testsSummary": summary.strip()}
    if suite.after_direct is not None:
        record.update(suite.after_direct(ws, case))
    return record


def governed(
    suite: Suite,
    case: dict[str, Any],
    condition: str,
    args: argparse.Namespace,
    rep: int,
) -> dict[str, Any]:
    run_dir = args.work / f"{suite.suite_id}-{case['id']}-{condition}-r{rep}-{stamp()}"
    ws = run_dir / "ws"
    ws.mkdir(parents=True)
    suite.build_baseline(ws)
    init_repo(ws)
    h = Harness(args.harness, harness_env(args.project_venv, run_dir / "harness-state"))
    h.run(ws, "init", "--path", ".", label="init")
    if git(ws, "status", "--porcelain").strip():
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "harness init")
    config_path = ws / ".harness" / "project.yaml"
    initial = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    builder = suite.conditions[condition]
    assert builder is not None
    config = builder(initial)
    change = run_dir / "change.json"
    write_text(change, json.dumps(change_document(case)))
    deviations = point_at_fixture(config, change, core=builder is core_condition)
    write_text(config_path, yaml.safe_dump(config, sort_keys=False))
    saved = args.out / "configs" / f"{condition}.yaml"
    if not saved.exists():
        # The exact file of the condition (CATALOGO.md §4.7), without local paths (§4.8); only
        # the path of the change file differs from case to case.
        text = yaml.safe_dump(config, sort_keys=False)
        for local, placeholder in ((str(run_dir), "<run>"), (str(REPO), "<repo>")):
            text = text.replace(local, placeholder)
        write_text(saved, text)
    database = state_database(h, ws)
    task = suite.task_for(case, condition)
    task_path = run_dir / "task.yaml"
    write_text(task_path, yaml.safe_dump(task, sort_keys=False, allow_unicode=True))
    create_code, _, _ = h.run(
        ws, "task", "create", "--path", ".", "--file", str(task_path), label="task create"
    )
    code, started, _ = h.run(
        ws, "run", "start", "--path", ".", "--task", task["taskId"], label="run start"
    )
    run_id = started.get("executionId", "") if isinstance(started, dict) else ""
    start_error = None
    if not run_id:
        start_error = json.dumps(started)[-1500:]
        _, latest, _ = h.run(ws, "status", "--path", ".", label="status latest")
        run_id = (
            ((latest.get("execution") or {}).get("executionId", ""))
            if isinstance(latest, dict)
            else ""
        )
    waits: list[str] = []
    code = simulated_person(h, ws, run_id, task["taskId"], run_dir, waits, code)
    _, status, _ = h.run(ws, "status", "--path", ".", "--run", run_id, label="status")
    status = status if isinstance(status, dict) else {}
    execution = status.get("execution") or {}
    gate = status.get("gate") or {}
    _, listed, _ = h.run(ws, "findings", "list", "--path", ".", "--run", run_id, label="findings")
    # One entry per (rule, severity, path, line): a correction round repeats the findings.
    unique = {
        (
            str(item.get("ruleId")),
            str(item.get("severity")),
            (item.get("location") or {}).get("path"),
            (item.get("location") or {}).get("startLine"),
        )
        for item in (listed if isinstance(listed, list) else [])
        if isinstance(item, dict)
    }
    findings = [
        {"rule": rule, "severity": severity, "path": path, "line": line}
        for rule, severity, path, line in sorted(
            unique, key=lambda f: (f[0], f[1], str(f[2]), f[3] or 0)
        )
    ]
    block = tuple(
        ((config.get("policies") or {}).get("findingBlockSeverities")) or BLOCK_SEVERITIES
    )
    relevant = [f for f in findings if f["rule"].startswith(suite.relevant)]
    blocking = [f for f in findings if f["severity"] in block]
    at_decision = execution.get("currentPhase") == "DECISION" and gate.get("status") == "PASSED"
    record: dict[str, Any] = {
        "taskCreateExitCode": create_code,
        "runExitCode": code,
        "runStartOutput": start_error,
        "waits": waits,
        "runStatus": execution.get("status"),
        "phase": execution.get("currentPhase"),
        "terminalReason": execution.get("terminalReason"),
        "gate": gate.get("status"),
        "gateReasons": gate.get("reasonCodes", []),
        "findings": findings,
        "suiteFindings": sorted({f"{f['severity']}:{f['rule']}" for f in relevant}),
        "blockingFindings": sorted({f"{f['severity']}:{f['rule']}" for f in blocking}),
        "hasBlockingFinding": bool(blocking),
        "suiteBlockingFinding": any(f["severity"] in block for f in relevant),
        "blocked": not at_decision,
        "detected": suite.detect(case, findings),
        "lanes": lanes(database, run_id),
        "deviations": deviations,
    }
    if suite.after_decision is not None:
        record.update(suite.after_decision(h, ws, run_id, status, case))
    record["steps"] = h.steps
    return record


def lanes(database: Path, run_id: str) -> dict[str, Any]:
    import sqlite3

    if not run_id or not database.exists():
        return {}
    connection = sqlite3.connect(database)
    try:
        rows = list(
            connection.execute(
                "select event_type, payload_json from events where execution_id=? "
                "and event_type like 'lane.%' order by execution_sequence",
                (run_id,),
            )
        )
    except sqlite3.Error:
        return {}
    finally:
        connection.close()
    classified = [json.loads(p).get("lane") for kind, p in rows if kind == "lane.classified"]
    return {
        "classified": classified[0] if classified else None,
        "escalated": any(kind == "lane.escalated" for kind, _ in rows),
    }


def run_one(
    suite: Suite, case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "suite": suite.suite_id,
        "case": case["id"],
        "set": case.get("set"),
        "kind": case["kind"],
        "group": case.get("group"),
        "expectedRule": case.get("rule"),
        "description": case.get("description"),
        "condition": condition,
        "rep": rep,
        "modelCalls": 0,
    }
    try:
        if suite.conditions[condition] is None:
            record.update(direct_equivalent(suite, case, args, rep))
        else:
            record.update(governed(suite, case, condition, args, rep))
    except Exception:  # noqa: BLE001 - the apparatus failed on this case: recorded, not hidden
        record["apparatusError"] = traceback.format_exc()[-2000:]
    print(
        suite.suite_id,
        case["id"],
        condition,
        rep,
        "ERROR" if "apparatusError" in record else "",
        record.get("delivered", ""),
        record.get("runStatus", ""),
        record.get("phase", ""),
        record.get("gate", ""),
        record.get("suiteFindings", ""),
        flush=True,
    )
    return record


# ----- summary ----------------------------------------------------------------------------------
def summarize(suite: Suite, records: list[dict[str, Any]]) -> dict[str, Any]:
    first = [r for r in records if r["rep"] == 1 and "apparatusError" not in r]
    by_condition: dict[str, Any] = {}
    for condition, builder in suite.conditions.items():
        rows = [r for r in first if r["condition"] == condition]
        if not rows:
            continue
        seeded = [r for r in rows if r["kind"] == "seeded"]
        clean = [r for r in rows if r["kind"] == "clean"]
        if builder is None:
            by_condition[condition] = {
                "seededDelivered": proportion(sum(r["delivered"] for r in seeded), len(seeded)),
                "cleanDelivered": proportion(sum(r["delivered"] for r in clean), len(clean)),
                "otherDelivered": {
                    kind: proportion(
                        sum(r["delivered"] for r in rows if r["kind"] == kind),
                        sum(1 for r in rows if r["kind"] == kind),
                    )
                    for kind in sorted({r["kind"] for r in rows} - {"seeded", "clean"})
                },
                "notDelivered": sorted(r["case"] for r in rows if not r["delivered"]),
            }
            continue
        groups: dict[str, Any] = {}
        for name in sorted({str(r["group"]) for r in rows if r["kind"] != "clean"}):
            items = [r for r in rows if str(r["group"]) == name and r["kind"] != "clean"]
            groups[name] = {
                "kind": sorted({r["kind"] for r in items}),
                "detected": proportion(sum(r["detected"] for r in items), len(items)),
                "blocked": proportion(sum(r["blocked"] for r in items), len(items)),
                "suiteBlockingFinding": proportion(
                    sum(r["suiteBlockingFinding"] for r in items), len(items)
                ),
                "missed": sorted(r["case"] for r in items if not r["detected"]),
            }
        by_condition[condition] = {
            "seeded": {
                "detected": proportion(sum(r["detected"] for r in seeded), len(seeded)),
                "blocked": proportion(sum(r["blocked"] for r in seeded), len(seeded)),
                "suiteBlockingFinding": proportion(
                    sum(r["suiteBlockingFinding"] for r in seeded), len(seeded)
                ),
                "notBlocked": sorted(r["case"] for r in seeded if not r["blocked"]),
            },
            "byGroup": groups,
            "ruleSpecificityOnClean": {
                rule: proportion(
                    sum(1 for r in clean if not any(f["rule"] == rule for f in r["findings"])),
                    len(clean),
                )
                for rule in suite.rules
            },
            "clean": {
                "withBlockingFinding": proportion(
                    sum(r["hasBlockingFinding"] for r in clean), len(clean)
                ),
                "withSuiteFinding": proportion(
                    sum(bool(r["suiteFindings"]) for r in clean), len(clean)
                ),
                "blockedAnyCause": proportion(sum(r["blocked"] for r in clean), len(clean)),
                "cases": {
                    r["case"]: {
                        "gate": r["gate"],
                        "phase": r["phase"],
                        "blocking": r["blockingFindings"],
                        "suiteFindings": r["suiteFindings"],
                    }
                    for r in clean
                },
            },
            "rulesObserved": rule_counts(rows),
        }
    by_case: dict[tuple[str, str], set[str]] = {}
    for r in records:
        if "apparatusError" in r:
            continue
        signature = json.dumps(
            [r.get(k) for k in ("delivered", "runStatus", "phase", "gate", "suiteFindings")]
        )
        by_case.setdefault((r["case"], r["condition"]), set()).add(signature)
    return {
        "suite": suite.suite_id,
        "title": suite.title,
        "directEquivalent": "D* applies the seeded change and runs the visible tests; it is a model of a direct agent, not an agent (CATALOGO.md §4.4)",
        "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION (a declared simulation)",
        "cases": {
            kind: sum(1 for c in suite.cases if c["kind"] == kind)
            for kind in sorted({c["kind"] for c in suite.cases})
        },
        "records": len(records),
        "apparatusErrors": sorted(
            f"{r['case']}/{r['condition']}/r{r['rep']}" for r in records if "apparatusError" in r
        ),
        "nonIdenticalRepetitions": sorted(
            f"{case}/{condition}"
            for (case, condition), values in by_case.items()
            if len(values) > 1
        ),
        "byCondition": by_condition,
        "notes": suite.notes,
    }


def rule_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """For every rule seen, in how many cases of each kind it appeared, by severity."""
    counts: dict[str, dict[str, int]] = {}
    for r in rows:
        for item in {(f["rule"], f["severity"]) for f in r["findings"]}:
            key = f"{item[1]}:{item[0]}"
            counts.setdefault(key, {})
            counts[key][r["kind"]] = counts[key].get(r["kind"], 0) + 1
    return dict(sorted(counts.items()))


# ----- command line -----------------------------------------------------------------------------
def main(
    suite: Suite,
    doc: str,
    summary_name: str,
    criteria: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    summarizer: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=doc, formatter_class=argparse.RawDescriptionHelpFormatter
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
    parser.add_argument("--only", default="", help="comma-separated case ids")
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
    stem = summary_name.removesuffix("-summary.json")
    records_path = args.out / f"{stem}.jsonl"
    if not args.summarize_only:
        wanted = [c for c in args.conditions.split(",") if c] or list(suite.conditions)
        unknown = sorted(set(wanted) - set(suite.conditions))
        if unknown:
            parser.error(f"unknown conditions: {', '.join(unknown)}")
        ids = {c for c in args.only.split(",") if c}
        cases = [c for c in suite.cases if not ids or c["id"] in ids]
        write_json(
            args.out / "environment.json",
            environment(
                args.harness,
                args.wheel,
                {
                    "suite": f"{suite.suite_id} {suite.title}",
                    "conditions": wanted,
                    "cases": [c["id"] for c in cases],
                    "reps": args.reps,
                    "projectVenv": args.project_venv.name,
                    "platformDeviations": (
                        ["runtime.agentSandbox: off (no write sandbox on Windows)"]
                        if WINDOWS
                        else []
                    ),
                },
            ),
        )
        records = [
            run_one(suite, case, condition, args, rep)
            for rep in range(1, args.reps + 1)
            for case in cases
            for condition in wanted
        ]
        write_jsonl(records_path, records)
    text = records_path.read_text(encoding="utf-8")
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    summary = summarizer(records) if summarizer else summarize(suite, records)
    summary["criteria"] = criteria(summary)
    write_json(args.out / summary_name, summary)
    return 1 if summary["apparatusErrors"] else 0
