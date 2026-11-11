"""Shared pieces of the resource and block suites H19, H20 and H44 (no model is called).

Each case runs on a fresh copy of the sample project (``suite_support.sample_project``, the
pricing function of N03-a and H18) with the fixture provider ``budget_agent.py``, whose
specification fixes what every call writes, reports and how long it takes. The helpers here set
up a governed workspace (``harness init``, the condition's configuration, the provider granted as
``conditions.grant_provider_command`` does), start the run with the declared simulated person
for the waits before DECISION, and read back what the harness recorded: the
``agent.call.recorded`` events of the agent contract, the budget events, the agent invocations
with their measured duration, and the fixture's own log of each call.

``main`` is the command line every suite shares (``--out``, ``--work``, ``--project-venv``,
``--harness``, ``--wheel``, ``--only``, ``--conditions``, ``--reps``, ``--summarize-only``), as
in ``interception.py``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import conditions  # noqa: E402
from common import (  # noqa: E402
    REPO,
    Harness,
    add_common_arguments,
    environment,
    git,
    stamp,
    state_database,
    write_json,
    write_jsonl,
)
from suite_support import (  # noqa: E402
    SAMPLE_TEST,
    WINDOWS,
    bin_dir,
    commit_init,
    configure,
    events,
    portable_env,
    read_log,
    sample_project,
    start_run,
    status_of,
)

AGENT = HERE / "budget_agent.py"
TRACE_HEADER = SAMPLE_TEST
TRACE_ASSERT = "apply_discount(100, 100, 0.1) == 90"
CORRECT_CODE = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)
OWNED = ["src/sample/pricing.py", "tests/test_pricing.py"]


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


def correct_spec() -> dict[str, Any]:
    """The correct discount and one test per requirement and criterion id (``traceTests``)."""
    return {
        "change": {"src/sample/pricing.py": CORRECT_CODE},
        "traceTests": {
            "path": "tests/test_pricing.py",
            "header": TRACE_HEADER,
            "assert": TRACE_ASSERT,
        },
    }


def generated_module(lines: int) -> str:
    """A Python module of exactly ``lines`` lines (constants), for the size by lines."""
    return "".join(f"VALUE_{index:05d} = {index}\n" for index in range(lines))


def baseline(root: Path, extra: dict[str, str] | None = None) -> None:
    """The sample project plus extra files, committed."""
    sample_project(root)
    if extra:
        for relative, content in extra.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))
        git(root, "add", "-A")
        git(root, "commit", "-qm", "baseline extra files")


def discount_task(
    task_id: str, requirements: int = 1, owned: list[str] | None = None, **extra: Any
) -> dict[str, Any]:
    """The threshold discount with ``requirements`` requirement variants (no security words)."""
    items = [
        {
            "requirementId": f"req_{index}",
            "text": f"Variant {index}: apply_discount(100, 100, 0.1) returns 90.",
        }
        for index in range(1, requirements + 1)
    ]
    task: dict[str, Any] = {
        "taskId": task_id,
        "title": "Threshold discount",
        "intent": "Apply the discount only when the subtotal meets the threshold.",
        "requirements": items,
        "acceptanceCriteria": [
            {"criterionId": "ac_threshold", "text": "apply_discount(100, 100, 0.1) returns 90."}
        ],
        "metadata": {"ownedPaths": list(owned or OWNED)},
    }
    task.update(extra)
    return task


class Utf8Harness(Harness):
    """``common.Harness`` that decodes the harness's output as UTF-8 (what it writes, also on
    Windows, where ``text=True`` alone would decode with the console code page and lose it)."""

    def run(
        self, cwd: Path, *args: str, label: str = "", timeout: float | None = None
    ) -> tuple[int, Any, float]:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                [self.executable, *args],
                cwd=cwd,
                env=self.env,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            code, stdout, stderr = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as expired:
            code = -9
            out = expired.stdout
            stdout = out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")
            stderr = "TIMEOUT"
        seconds = time.monotonic() - started
        payload: Any = {"stdout": stdout[-800:], "stderr": stderr[-800:]}
        for stream in (stdout, stderr):
            try:
                payload = json.loads(stream)
                break
            except (json.JSONDecodeError, TypeError):
                continue
        self.steps.append(
            {"step": label or " ".join(args[:2]), "exitCode": code, "seconds": round(seconds, 3)}
        )
        return code, payload, seconds


# ----- one governed workspace ------------------------------------------------------------------
@dataclass
class Workspace:
    run_dir: Path
    ws: Path
    h: Harness
    config: dict[str, Any]
    deviations: list[str]
    spec_path: Path
    log: Path
    command: list[str]
    database: Path
    extra: dict[str, Any] = field(default_factory=dict)

    def write_spec(self, spec: dict[str, Any], log_name: str = "calls.jsonl") -> None:
        """(Re)write the fixture's specification; a new ``log_name`` starts a new call count."""
        self.log = self.run_dir / log_name
        self.spec_path.write_text(json.dumps({**spec, "log": str(self.log)}), encoding="utf-8")


def governed(
    run_dir: Path,
    args: argparse.Namespace,
    spec: dict[str, Any],
    *,
    extra_files: dict[str, str] | None = None,
    mutate: Callable[[dict[str, Any]], None] | None = None,
    core: bool = False,
    grant: bool = True,
    launcher: str = "python",
) -> Workspace:
    """``harness init`` over a fresh sample project, the fixture provider, the condition's
    configuration (``mutate``) and, unless ``grant`` is false, the grant of its command.

    ``launcher`` is the executable that starts the fixture: ``python`` (found on ``PATH``, the
    project's virtual environment) unless a case needs a command no profile rule names (H44
    B12: the Python profile grants ``process.execute: [python]`` by exact name)."""
    ws = run_dir / "ws"
    ws.mkdir(parents=True)
    baseline(ws, extra_files)
    h = Utf8Harness(
        args.harness, portable_env([bin_dir(args.project_venv)], run_dir / "harness-state")
    )
    h.run(ws, "init", "--path", ".", label="init")
    commit_init(ws)
    spec_path = run_dir / "fixture-spec.json"
    command = [launcher, str(AGENT), "--spec", str(spec_path)]

    def condition(config: dict[str, Any]) -> None:
        if not grant:
            capabilities = config.get("capabilities") or {}
            key = "grants" if core else "extend"
            rule = conditions.provider_grant(command)
            capabilities[key] = [r for r in capabilities.get(key) or [] if r != rule]
        if mutate is not None:
            mutate(config)

    config, deviations = configure(ws, command, core=core, mutate=condition)
    workspace = Workspace(
        run_dir, ws, h, config, deviations, spec_path, run_dir / "calls.jsonl", command, Path()
    )
    workspace.write_spec(spec)
    workspace.database = state_database(h, ws)
    return workspace


def saved_config(args: argparse.Namespace, name: str, workspace: Workspace) -> None:
    """The exact ``project.yaml`` of a condition (``CATALOGO.md`` §4.7), without local paths."""
    saved = args.out / "configs" / f"{name}.yaml"
    if saved.exists():
        return
    text = yaml.safe_dump(workspace.config, sort_keys=False)
    for local, placeholder in ((str(workspace.run_dir), "<run>"), (str(REPO), "<repo>")):
        text = text.replace(local, placeholder)
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_bytes(text.encode("utf-8"))


def run_task(workspace: Workspace, task: dict[str, Any]) -> dict[str, Any]:
    """``task create`` + ``run start`` + the simulated person; the run id and exit code."""
    return start_run(workspace.h, workspace.ws, workspace.run_dir, task)


def contract_calls(database: Path, run_id: str) -> list[dict[str, Any]]:
    return [
        {"sequence": e["sequence"], **e["payload"]}
        for e in events(database, run_id)
        if e["type"] == "agent.call.recorded"
    ]


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def invocation_seconds(database: Path, run_id: str) -> dict[str, float]:
    """The duration the harness measured for each agent invocation of the run."""
    if not database.exists():
        return {}
    connection = sqlite3.connect(database)
    try:
        rows = list(
            connection.execute(
                "SELECT record_id, payload_json FROM records "
                "WHERE record_type='agent_invocation' AND execution_id=?",
                (run_id,),
            )
        )
    except sqlite3.Error:
        return {}
    finally:
        connection.close()
    seconds: dict[str, float] = {}
    for record_id, payload in rows:
        data = json.loads(payload)
        started = _instant(data.get("startedAt") or data.get("started_at"))
        finished = _instant(data.get("finishedAt") or data.get("finished_at"))
        if started and finished:
            seconds[str(data.get("invocationId") or data.get("invocation_id") or record_id)] = (
                round((finished - started).total_seconds(), 3)
            )
    return seconds


def finding_items(h: Harness, ws: Path, run_id: str) -> list[dict[str, Any]]:
    _, listed, _ = h.run(ws, "findings", "list", "--path", ".", "--run", run_id, label="findings")
    if not isinstance(listed, list):
        return []
    items = []
    for item in listed:
        if not isinstance(item, dict):
            continue
        location = item.get("location") or {}
        items.append(
            {
                "rule": item.get("ruleId"),
                "severity": item.get("severity"),
                "path": location.get("path"),
                "line": location.get("startLine"),
                "message": str(item.get("message") or "")[:400],
                "recommendation": str(item.get("recommendation") or "")[:400],
            }
        )
    return items


def run_state(workspace: Workspace, run_id: str) -> dict[str, Any]:
    status = status_of(workspace.h, workspace.ws, run_id)
    execution = status.get("execution") or {}
    gate = status.get("gate") or {}
    return {
        "runStatus": execution.get("status"),
        "phase": execution.get("currentPhase"),
        "terminalReason": execution.get("terminalReason"),
        "gate": gate.get("status"),
        "gateReasons": gate.get("reasonCodes", []),
        "changeSetDigest": execution.get("changeSetDigest"),
        "blockedPhases": [
            f"{p.get('phaseId')}:{p.get('status')}:{str(p.get('summary') or '')[:400]}"
            for p in status.get("phases") or []
            if p.get("status") not in {"PASSED", None}
        ],
    }


def cleanup(run_dir: Path, keep: bool) -> None:
    """Remove a case's directory (the work tree of a long suite grows fast) unless kept."""
    if keep:
        return
    from suite_support import rmtree

    with contextlib.suppress(OSError):
        rmtree(run_dir)


# ----- command line ----------------------------------------------------------------------------
Runner = Callable[[dict[str, Any], str, argparse.Namespace, int], dict[str, Any]]


def main(
    *,
    doc: str,
    suite_id: str,
    title: str,
    stem: str,
    cases: list[dict[str, Any]],
    conditions_of: Callable[[dict[str, Any]], list[str]],
    all_conditions: list[str],
    runner: Runner,
    summarize: Callable[[list[dict[str, Any]]], dict[str, Any]],
    extra_environment: Callable[[argparse.Namespace], dict[str, Any]] | None = None,
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
        help="venv whose python runs the project's tests (pytest) and the fixture",
    )
    parser.add_argument("--only", default="", help="comma-separated case ids")
    parser.add_argument("--conditions", default="", help="comma-separated condition names")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--summarize-only", action="store_true")
    parser.add_argument("--keep-work", action="store_true", help="keep each case's directory")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.work = (args.work or args.out / ".work").resolve()
    args.project_venv = args.project_venv.resolve()
    if not args.summarize_only and not shutil.which(args.harness):
        parser.error(f"harness executable not found: {args.harness}")
    args.work.mkdir(parents=True, exist_ok=True)
    records_path = args.out / f"{stem}.jsonl"
    if not args.summarize_only:
        wanted = [c for c in args.conditions.split(",") if c] or list(all_conditions)
        unknown = sorted(set(wanted) - set(all_conditions))
        if unknown:
            parser.error(f"unknown conditions: {', '.join(unknown)}")
        ids = {c for c in args.only.split(",") if c}
        unknown_ids = sorted(ids - {c["id"] for c in cases})
        if unknown_ids:
            parser.error(f"unknown cases: {', '.join(unknown_ids)}")
        selected = [c for c in cases if not ids or c["id"] in ids]
        extra = extra_environment(args) if extra_environment else {}
        args.context = extra
        write_json(
            args.out / "environment.json",
            environment(
                args.harness,
                args.wheel,
                {
                    "suite": f"{suite_id} {title}",
                    "conditions": wanted,
                    "cases": [c["id"] for c in selected],
                    "reps": args.reps,
                    "projectVenv": args.project_venv.name,
                    "platformDeviations": (
                        ["runtime.agentSandbox: off (no write sandbox on Windows)"]
                        if WINDOWS
                        else []
                    ),
                    **extra,
                },
            ),
        )
        records: list[dict[str, Any]] = []
        for rep in range(1, args.reps + 1):
            for case in selected:
                for condition in conditions_of(case):
                    if condition not in wanted:
                        continue
                    record: dict[str, Any] = {
                        "suite": suite_id,
                        "case": case["id"],
                        "condition": condition,
                        "rep": rep,
                        "modelCalls": 0,
                    }
                    try:
                        record.update(runner(case, condition, args, rep))
                    except Exception:  # noqa: BLE001 - the apparatus failed: recorded, not hidden
                        record["apparatusError"] = traceback.format_exc()[-2000:]
                    records.append(record)
                    print(
                        suite_id,
                        case["id"],
                        condition,
                        rep,
                        "ERROR" if "apparatusError" in record else "",
                        record.get("runStatus", ""),
                        record.get("phase", ""),
                        record.get("brief", ""),
                        flush=True,
                    )
                    # Written after every case, so an interrupted suite keeps what it measured.
                    write_jsonl(records_path, records)
    text = records_path.read_text(encoding="utf-8")
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    summary = summarize(records)
    summary["apparatusErrors"] = sorted(
        f"{r['case']}/{r['condition']}/r{r['rep']}" for r in records if "apparatusError" in r
    )
    summary["records"] = len(records)
    write_json(args.out / f"{stem}-summary.json", summary)
    return 0


def stamp_dir(args: argparse.Namespace, *parts: str) -> Path:
    safe = "-".join(p.replace("*", "star") for p in parts)
    run_dir = args.work / f"{safe}-{stamp()}"
    run_dir.mkdir(parents=True)
    return run_dir


def environment_python(args: argparse.Namespace) -> str:
    return str(bin_dir(args.project_venv) / ("python.exe" if os.name == "nt" else "python"))


__all__ = [
    "OWNED",
    "Utf8Harness",
    "Workspace",
    "baseline",
    "cleanup",
    "contract_calls",
    "correct_spec",
    "discount_task",
    "environment_python",
    "finding_items",
    "generated_module",
    "governed",
    "invocation_seconds",
    "main",
    "proportion",
    "read_log",
    "run_state",
    "run_task",
    "saved_config",
    "stamp_dir",
    "wilson",
]
