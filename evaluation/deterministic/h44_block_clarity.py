#!/usr/bin/env python3
"""H44: clarity and actionability of blocks (``CATALOGO.md`` §2.8, ``PREREGISTRO.md``). No model.

Twenty block states reproduced on the sample project with the fixture provider
``budget_agent.py``: fifteen runs that stop (B01-B15, documented exit 6) and five refused
commands (R01-R05). For each state the suite reads what a person sees (the output of the command
that stopped, ``status``, ``findings list``, ``inbox --json`` and the decision brief
``review --run --json``; for a refusal, its error and hint) and applies a deterministic rubric:

* R1 the cause declared for the state (a rule id or text, ``PREREGISTRO.md``) appears;
* R2 the declared location appears (path, and a line when declared): not applicable to causes
  that are not about a file;
* R3 at least one next ``harness ...`` command appears (a ``next`` or ``hint`` field, or the text
  of the cause, a recommendation or a summary; in prose, "harness" must be followed by a
  top-level command of ``docs/reference/cli.md``, so "the harness push ..." is not a command);
* R4 the exit code is the one ``docs/reference/exit-codes.md`` documents for the state.

Then, after everything was read, the commands of the ``next`` and ``hint`` fields are run in the
case's workspace in the order shown (``"..."`` replaced by a fixed rationale; a command with a
``<placeholder>`` or marked "interactive on a terminal" is not run and counted apart), and the
commands named only in prose are checked with ``--help``. A usage failure is exit 2 with the
CLI's usage text, or exit 3 (not found). A case that does not block (DECISION with the gate
``PASSED``, or a refusal that exits 0) is reported as "no block" and left out of the rubric's
denominator. The human 1-5 rating of the catalogue is pending (a judgement, reported apart).

Usage:
  python evaluation/deterministic/h44_block_clarity.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only B01,R01,...]
      [--conditions H] [--reps 1] [--summarize-only]

Writes ``DIR/h44-block-clarity.jsonl``, ``DIR/h44-block-clarity-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import REPO, SIMULATED_PERSON, Harness, simulated_person  # noqa: E402
from resource_support import (  # noqa: E402
    CORRECT_CODE,
    OWNED,
    Utf8Harness,
    Workspace,
    cleanup,
    correct_spec,
    discount_task,
    governed,
    main,
    proportion,
    run_state,
    saved_config,
    stamp_dir,
)
from suite_support import WINDOWS, bin_dir, sample_change  # noqa: E402

EXIT_CODES_DOC = REPO / "docs" / "reference" / "exit-codes.md"
CLI_DOC = REPO / "docs" / "reference" / "cli.md"
RATIONALE = "evaluation rationale"
STOP_LABELS = ("task create", "run start", "run continue")
USAGE_TEXT = ("Usage:", "No such option", "Missing option", "Invalid value", "No such command")
# An access-key id of the AWS shape, assembled here so that no literal key is in the repository.
FAKE_KEY = "AK" + "IA" + "Q7LMZ3R2" + "XWV5N8KD"
REDUCED_TIME = {
    size: {"tokens": tokens, "wallSeconds": seconds}
    for size, (tokens, seconds) in {"S": (2_000, 6), "M": (8_000, 8), "L": (32_000, 10)}.items()
}


class RecordingHarness(Utf8Harness):
    """``common.Harness`` that also keeps each command's arguments, exit code and output."""

    def __init__(self, executable: str, env: dict[str, str]) -> None:
        super().__init__(executable, env)
        self.outputs: list[dict[str, Any]] = []

    def run(
        self, cwd: Path, *args: str, label: str = "", timeout: float | None = None
    ) -> tuple[int, Any, float]:
        code, payload, seconds = super().run(cwd, *args, label=label, timeout=timeout)
        self.outputs.append({"label": label, "args": list(args), "code": code, "payload": payload})
        return code, payload, seconds


# ----- the states ------------------------------------------------------------------------------
def with_code(code: str) -> dict[str, Any]:
    spec = correct_spec()
    spec["change"] = {"src/sample/pricing.py": code}
    return spec


def state(
    case_id: str,
    kind: str,
    description: str,
    cause: str,
    *,
    spec: dict[str, Any] | None = None,
    location: tuple[str, bool] | None = None,
    exit_code: int = 6,
    mutate: Callable[[dict[str, Any]], None] | None = None,
    grant: bool = True,
    outside_profile: bool = False,
    task: dict[str, Any] | None = None,
    answer: bool = True,
    action: str | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "kind": kind,
        "description": description,
        "cause": cause,
        "spec": spec or correct_spec(),
        "location": location,
        "exitCode": exit_code,
        "mutate": mutate,
        "grant": grant,
        "outsideProfile": outside_profile,
        "task": task,
        "answer": answer,
        "action": action,
    }


def set_iterations(config: dict[str, Any]) -> None:
    config.setdefault("agentContract", {})["maxIterations"] = 1


def block_dependency(config: dict[str, Any]) -> None:
    config.setdefault("verification", {}).setdefault("riskFactors", {})["newDependency"] = "block"


def reduced_time(config: dict[str, Any]) -> None:
    config.setdefault("agentContract", {})["budgets"] = REDUCED_TIME


def enforce_criteria(config: dict[str, Any]) -> None:
    config.setdefault("intake", {})["criteriaPolicy"] = "enforce"


SECRET_CODE = CORRECT_CODE + f'\n\nRATES_ACCESS_KEY = "{FAKE_KEY}"\n'
PICKLE_CODE = (
    "import pickle\n\n\n"
    + CORRECT_CODE
    + "\n\ndef load_rates(data: bytes) -> dict:\n    return pickle.loads(data)\n"
)
FAILING = sample_change(correct=False)
NO_CRITERIA = {
    "taskId": "task_h44_no_criteria",
    "title": "Threshold discount",
    "intent": "Apply the discount only when the subtotal meets the threshold.",
    "requirements": [
        {"requirementId": "req_1", "text": "apply_discount(100, 100, 0.1) returns 90."}
    ],
    "metadata": {"ownedPaths": OWNED},
}
VAGUE = {
    "taskId": "task_h44_vague",
    "title": "Threshold discount",
    "intent": "Make the discount better.",
    "acceptanceCriteria": [{"criterionId": "ac_works", "text": "It works."}],
    "metadata": {"ownedPaths": OWNED},
}

CASES: list[dict[str, Any]] = [
    state(
        "B01",
        "run",
        "a change whose test fails",
        r"test_req_threshold_ac_threshold",
        spec={"change": FAILING},
        location=("tests/test_pricing.py", False),
    ),
    state(
        "B02",
        "run",
        "a literal access key of the AWS shape (assembled from parts)",
        r"secrets\.",
        spec=with_code(SECRET_CODE),
        location=("src/sample/pricing.py", True),
    ),
    state(
        "B03",
        "run",
        "the test file deleted",
        r"weakened\.test-deleted",
        spec={
            "change": {"src/sample/pricing.py": CORRECT_CODE},
            "delete": ["tests/test_pricing.py"],
        },
        location=("tests/test_pricing.py", False),
    ),
    state(
        "B04",
        "run",
        "pickle.loads on input",
        r"security\.unsafe-deserialization",
        spec=with_code(PICKLE_CODE),
        location=("src/sample/pricing.py", True),
    ),
    state(
        "B05",
        "run",
        "a write outside ownedPaths (README.md)",
        r"workspace\.outside-owned-paths",
        spec={
            **correct_spec(),
            "change": {
                "src/sample/pricing.py": CORRECT_CODE,
                "README.md": "# Sample\n\nThreshold discount.\n",
            },
        },
        location=("README.md", False),
    ),
    state(
        "B06",
        "run",
        "a new dependency under riskFactors.newDependency: block",
        r"risk\.newDependency",
        spec={
            **correct_spec(),
            "change": {
                "src/sample/pricing.py": CORRECT_CODE,
                "requirements.txt": "requests==2.32.3\n",
            },
        },
        location=("requirements.txt", False),
        # Amendment 2026-11-11 (2): a blocking risk factor is a non-mandatory finding, so the
        # run waits at DECISION for an exception (exit 4, docs/reference/exit-codes.md).
        exit_code=4,
        mutate=block_dependency,
        task=discount_task("task_h44_b06", owned=[*OWNED, "requirements.txt"]),
    ),
    state(
        "B07",
        "run",
        "the first call costs more than budget.perCall",
        r"budget\.exceeded",
        spec={**correct_spec(), "costs": [26.0], "defaultCost": 0.01},
    ),
    state(
        "B08",
        "run",
        "a call reports twice its token budget",
        r"agent\.limit\.reached",
        spec={**correct_spec(), "tokens": {"factor": 2.0}},
    ),
    state(
        "B09",
        "run",
        "a call sleeps twice its time limit (the reduced budgets of H19)",
        r"agent\.limit\.reached",
        spec={**correct_spec(), "sleep": {"factor": 2.0}},
        mutate=reduced_time,
    ),
    state(
        "B10",
        "run",
        "a failing change with agentContract.maxIterations: 1",
        r"agent\.iterations\.exhausted",
        spec={"change": FAILING},
        mutate=set_iterations,
    ),
    state(
        "B11",
        "run",
        "the clarify answer carries a key outside its contract (a 6-requirement task, so INTENT "
        "makes a clarify call)",
        r"agent\.contract\.violation",
        spec={
            **correct_spec(),
            "inject": {"kind": "clarify", "result": {"questions": [], "verdict": "ok"}},
        },
        task=discount_task("task_h44_b11", requirements=6),
    ),
    state(
        "B12",
        "run",
        "the provider command has no grant: the fixture is started by the absolute path of the "
        "project's interpreter, which no profile rule names (the Python profile grants "
        "process.execute: [python] by exact name), and the provider's own grant is removed",
        r"capabilities\.command-denied",
        grant=False,
        outside_profile=True,
    ),
    # Amendment 2026-11-11 (2): the questions are left unanswered, as in B14; an answer gives
    # the task criteria and the C0 block the state declares is gone.
    state(
        "B13",
        "run",
        "a task without acceptance criteria, questions left unanswered",
        r"\bC0\b",
        task=NO_CRITERIA,
        answer=False,
    ),
    state(
        "B14",
        "run",
        "a vague criterion under intake.criteriaPolicy: enforce, questions left unanswered",
        r"\b(C1|C2|C3|T1)\b",
        task=VAGUE,
        mutate=enforce_criteria,
        answer=False,
    ),
    state(
        "B15",
        "run",
        "REJECT by the simulated person at DECISION (declared)",
        r"REJECT",
        action="reject",
    ),
    state(
        "R01",
        "refusal",
        "APPROVE over the failed gate of B02",
        r"FAILED|not pass|did not pass",
        spec=with_code(SECRET_CODE),
        exit_code=5,
        action="approve",
    ),
    state(
        "R02",
        "refusal",
        "APPROVE with a digest that is not the ChangeSet's",
        r"digest",
        exit_code=5,
        action="stale-digest",
    ),
    state(
        "R03",
        "refusal",
        "budget raise with an empty rationale (after B07)",
        r"rationale",
        spec={**correct_spec(), "costs": [26.0], "defaultCost": 0.01},
        exit_code=5,
        action="raise-empty",
    ),
    state(
        "R04",
        "refusal",
        "gate decide by the actor agent.fixture",
        r"agent",
        exit_code=5,
        action="agent-actor",
    ),
    state(
        "R05",
        "refusal",
        "harness verify after the payload of an event was edited in the database",
        r"chain|digest",
        exit_code=6,
        action="tamper",
    ),
]


def conditions_of(case: dict[str, Any]) -> list[str]:
    del case
    return ["H"]


# ----- surfaces and rubric ---------------------------------------------------------------------
def documented_codes() -> list[int]:
    text = EXIT_CODES_DOC.read_text(encoding="utf-8")
    return sorted({int(m) for m in re.findall(r"^\| `(\d+)` \|", text, flags=re.M)})


def known_commands() -> set[str]:
    """The top-level commands of the CLI reference (``## harness <command> ...``): a word after
    "harness" in prose counts as a command only when it is one of them."""
    text = CLI_DOC.read_text(encoding="utf-8")
    return set(re.findall(r"^## harness ([a-z][a-z-]*)", text, flags=re.M))


def strings(value: Any, keys: tuple[str, ...] | None = None, inside: bool = False) -> list[str]:
    """Every string in ``value``; with ``keys``, only those under one of those keys."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found += strings(item, keys, inside or (keys is not None and key in keys))
    elif isinstance(value, list):
        for item in value:
            found += strings(item, keys, inside)
    elif isinstance(value, str) and (keys is None or inside):
        found.append(value)
    return found


COMMAND = re.compile(r"harness [a-z][a-z-]*(?: [^;`'\"\n()]*)?")


def commands_in(texts: list[str]) -> list[str]:
    out: list[str] = []
    for text in texts:
        for part in re.split(r"[;\n]", text):
            for match in COMMAND.finditer(part):
                command = match.group(0).strip().rstrip(".,:")
                if command not in out:
                    out.append(command)
    return out


def structured_commands(surfaces: dict[str, Any]) -> list[str]:
    """The commands of the ``next`` and ``hint`` fields, as written (one per ``;``)."""
    out: list[str] = []
    for text in strings(surfaces, ("next", "hint")):
        for backticked in re.findall(r"`(harness [^`]+)`", text) or [text]:
            for part in backticked.split(";"):
                part = part.strip()
                if part.startswith("harness ") and part not in out:
                    out.append(part)
    return out


def location_found(surfaces: dict[str, Any], location: tuple[str, bool]) -> bool:
    path, with_line = location
    for item in surfaces.get("findings") or []:
        if isinstance(item, dict):
            where = item.get("location") or {}
            if where.get("path") == path and (not with_line or where.get("startLine")):
                return True
    text = json.dumps(surfaces)
    if with_line:
        return re.search(re.escape(path) + r":\d+", text) is not None
    return path in text


def rubric(case: dict[str, Any], surfaces: dict[str, Any], code: int | None) -> dict[str, Any]:
    text = json.dumps(surfaces, ensure_ascii=False)
    cause = re.search(case["cause"], text) is not None
    location = None if case["location"] is None else location_found(surfaces, case["location"])
    structured = structured_commands(surfaces)
    known = known_commands()
    prose = [c for c in commands_in(strings(surfaces)) if c.split()[1] in known]
    command = bool(structured or prose)
    exit_ok = code == case["exitCode"]
    return {
        "R1cause": cause,
        "R2location": location,
        "R3command": command,
        "R4exitCode": exit_ok,
        "meets": cause and location is not False and command and exit_ok,
        "structuredCommands": structured,
        "proseCommands": [c for c in prose if c not in structured],
    }


def read_surfaces(h: Harness, ws: Path, run_id: str) -> dict[str, Any]:
    _, status, _ = h.run(ws, "status", "--path", ".", "--run", run_id, label="status")
    _, findings, _ = h.run(ws, "findings", "list", "--path", ".", "--run", run_id, label="findings")
    _, inbox, _ = h.run(ws, "inbox", "--path", ".", "--json", label="inbox")
    _, review, _ = h.run(ws, "review", "--path", ".", "--run", run_id, "--json", label="review")
    pending = (
        [i for i in inbox if isinstance(i, dict) and i.get("executionId") == run_id]
        if isinstance(inbox, list)
        else inbox
    )
    return {"status": status, "findings": findings, "inbox": pending, "review": review}


# ----- running the suggested commands ----------------------------------------------------------
def runnable(command: str) -> tuple[list[str] | None, str | None]:
    """The arguments of a suggested command, or why it is not run."""
    if "interactive on a terminal" in command:
        return None, "interactive"
    text = re.sub(r"\s*\((?:after|interactive)[^)]*\)\s*$", "", command)
    text = re.sub(r"\s+\(.*\)\s*$", "", text)
    if re.search(r"<[^>]+>", text):
        return None, "placeholder"
    text = text.replace('"..."', f'"{RATIONALE}"').replace("'...'", f"'{RATIONALE}'")
    try:
        words = shlex.split(text)
    except ValueError:
        return None, "unparsable"
    return (words[1:], None) if words and words[0] == "harness" else (None, "not harness")


def usage_failure(code: int, payload: Any) -> bool:
    text = json.dumps(payload)
    return code == 3 or (code == 2 and any(marker in text for marker in USAGE_TEXT))


def execute(h: Harness, ws: Path, structured: list[str], prose: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in structured:
        words, skipped = runnable(command)
        if words is None:
            results.append({"command": command, "source": "structured", "skipped": skipped})
            continue
        code, payload, _ = h.run(ws, *words, label="suggested", timeout=600)
        results.append(
            {
                "command": command,
                "source": "structured",
                "exitCode": code,
                "usageFailure": usage_failure(code, payload),
                "output": json.dumps(payload)[-300:],
            }
        )
    for command in prose:
        words = command.split()[1:3]
        code, payload, _ = h.run(ws, *words, "--help", label="suggested --help", timeout=120)
        if code != 0 and len(words) > 1:
            code, payload, _ = h.run(ws, words[0], "--help", label="suggested --help", timeout=120)
        results.append(
            {
                "command": command,
                "source": "prose",
                "helpExitCode": code,
                "subcommandExists": code == 0,
            }
        )
    return results


# ----- one state -------------------------------------------------------------------------------
def decide(
    h: Harness, ws: Path, run_id: str, decision: str, digest: str, actor: str
) -> tuple[int, Any]:
    code, payload, _ = h.run(
        ws,
        "gate",
        "decide",
        "--path",
        ".",
        "--run",
        run_id,
        "--decision",
        decision,
        "--change-set-digest",
        digest,
        "--actor",
        actor,
        "--rationale",
        RATIONALE,
        label=f"gate decide {decision}",
    )
    return code, payload


def tamper(database: Path, run_id: str) -> str:
    """Edit the payload of one event of the run (not its digest), as the N13 probes do."""
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT global_sequence, payload_json FROM events WHERE execution_id=? "
            "AND event_type='phase.completed' ORDER BY execution_sequence LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            return "no phase.completed event"
        payload = json.loads(row[1])
        payload["summary"] = str(payload.get("summary") or "") + " (edited)"
        connection.execute(
            "UPDATE events SET payload_json=? WHERE global_sequence=?",
            (json.dumps(payload), row[0]),
        )
        connection.commit()
        return f"event {row[0]}: summary edited"
    finally:
        connection.close()


def start(workspace: Workspace, task: dict[str, Any], answer: bool) -> tuple[str, int]:
    h, ws = workspace.h, workspace.ws
    path = workspace.run_dir / "task.yaml"
    import yaml

    path.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    create_code, _, _ = h.run(
        ws, "task", "create", "--path", ".", "--file", str(path), label="task create"
    )
    if create_code != 0:
        return "", create_code
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
    if answer:
        code = simulated_person(h, ws, run_id, task["taskId"], workspace.run_dir, [], code)
    return run_id, code


def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    del rep
    run_dir = stamp_dir(args, "h44", case["id"], condition)
    launcher = "python"
    if case["outsideProfile"]:
        launcher = str(
            bin_dir(args.project_venv.resolve()) / ("python.exe" if WINDOWS else "python")
        )
    workspace = governed(
        run_dir, args, case["spec"], mutate=case["mutate"], grant=case["grant"], launcher=launcher
    )
    recording = RecordingHarness(workspace.h.executable, workspace.h.env)
    recording.steps = workspace.h.steps
    workspace.h = recording
    saved_config(args, f"{condition}-{case['id']}", workspace)
    task = case["task"] or discount_task(f"task_h44_{case['id'].lower()}")
    run_id, code = start(workspace, task, case["answer"])
    stops = [o for o in recording.outputs if o["label"] in STOP_LABELS]
    stop = stops[-1] if stops else {}
    # The exit code a person sees is that of the run command that stopped, not that of a
    # command the simulated person tried afterwards.
    code = stop.get("code", code)
    state_now = run_state(workspace, run_id) if run_id else {}
    action = case["action"]
    digest = str(state_now.get("changeSetDigest") or "")
    note = None
    if action == "reject" and state_now.get("phase") == "DECISION":
        code, _ = decide(recording, workspace.ws, run_id, "REJECT", digest, SIMULATED_PERSON)
        stop = recording.outputs[-1]
    elif action == "approve" and state_now.get("changeSetDigest"):
        # Amendment 2026-11-11 (2): B02's run fails in VERIFICATION and never reaches DECISION;
        # the person tries to approve it there (its ChangeSet exists), which is R01's state.
        code, _ = decide(recording, workspace.ws, run_id, "APPROVE", digest, SIMULATED_PERSON)
        stop = recording.outputs[-1]
    elif action == "stale-digest" and state_now.get("phase") == "DECISION":
        code, _ = decide(
            recording, workspace.ws, run_id, "APPROVE", "sha256:" + "0" * 64, SIMULATED_PERSON
        )
        stop = recording.outputs[-1]
    elif action == "agent-actor" and state_now.get("phase") == "DECISION":
        code, _ = decide(recording, workspace.ws, run_id, "APPROVE", digest, "agent.fixture")
        stop = recording.outputs[-1]
    elif action == "raise-empty":
        code, _, _ = recording.run(
            workspace.ws,
            "budget",
            "raise",
            "--path",
            ".",
            "--run",
            run_id,
            "--scope",
            "call",
            "--metric",
            "costUsd",
            "--to",
            "100",
            "--rationale",
            " ",
            "--actor",
            SIMULATED_PERSON,
            label="budget raise",
        )
        stop = recording.outputs[-1]
    elif action == "tamper":
        note = tamper(workspace.database, run_id)
        code, _, _ = recording.run(
            workspace.ws, "verify", "--path", ".", "--run", run_id, label="verify"
        )
        stop = recording.outputs[-1]
    elif action is not None:
        note = f"the run did not reach DECISION ({state_now.get('runStatus')}:{state_now.get('phase')}); the action was not taken"
        stop = {"label": "not taken", "code": None, "payload": None}
        code = None
    not_evaluated = note is not None and stop.get("label") == "not taken"
    if action is not None and run_id and not_evaluated is False:
        state_now = {**state_now, **run_state(workspace, run_id)}
    stop_view = {"stop": stop.get("payload")}
    if case["kind"] == "refusal":
        surfaces = dict(stop_view)
    else:
        surfaces = {
            **stop_view,
            **(read_surfaces(recording, workspace.ws, run_id) if run_id else {}),
        }
    blocked = (
        code not in (0, None)
        if case["kind"] == "refusal"
        else not (state_now.get("phase") == "DECISION" and state_now.get("gate") == "PASSED")
    )
    if case["kind"] == "run" and action == "reject":
        blocked = code is not None
    graded = rubric(case, surfaces, code)
    strict = rubric(case, stop_view, code)
    executed = execute(
        recording, workspace.ws, graded["structuredCommands"], graded["proseCommands"]
    )
    record = {
        "kind": case["kind"],
        "description": case["description"],
        "expectedCause": case["cause"],
        "expectedLocation": case["location"],
        "expectedExitCode": case["exitCode"],
        "observedExitCode": code,
        "stopCommand": stop.get("label"),
        "exitCodeDocumented": code in documented_codes(),
        "isBlock": blocked and not not_evaluated,
        "notEvaluated": not_evaluated,
        "note": note,
        "runId": bool(run_id),
        **state_now,
        "rubric": graded,
        "strictRubric": {
            k: strict[k] for k in ("R1cause", "R2location", "R3command", "R4exitCode", "meets")
        },
        "suggested": executed,
        "surfaces": json.dumps(surfaces, ensure_ascii=False)[:6000],
        "deviations": workspace.deviations,
        "steps": list(recording.steps),
        "brief": f"block={blocked} meets={graded['meets']}",
    }
    cleanup(run_dir, args.keep_work)
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    blocks = [r for r in ok if r.get("isBlock")]
    not_evaluated = sorted(r["case"] for r in ok if r.get("notEvaluated"))
    items = ("R1cause", "R2location", "R3command", "R4exitCode", "meets")

    def rate(rows: list[dict[str, Any]], key: str, field: str = "rubric") -> dict[str, Any]:
        applicable = [r for r in rows if r[field][key] is not None]
        return proportion(sum(1 for r in applicable if r[field][key]), len(applicable))

    executed = [s for r in blocks for s in r.get("suggested") or [] if "exitCode" in s]
    skipped = [s for r in blocks for s in r.get("suggested") or [] if "skipped" in s]
    prose = [s for r in blocks for s in r.get("suggested") or [] if s.get("source") == "prose"]
    meets = rate(blocks, "meets")
    summary = {
        "suite": "H44",
        "title": "clarity and actionability of blocks",
        "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION (not "
        "the INTENT questions of B13 and B14) and takes the declared actions of B15 and R01-R04 (a declared simulation)",
        "humanRating": "pending: the 1-5 rating by two people (a judgement) is not part of this "
        "deterministic suite",
        "states": {
            r["case"]: {
                "kind": r["kind"],
                "isBlock": r.get("isBlock"),
                "observedExitCode": r.get("observedExitCode"),
                "expectedExitCode": r.get("expectedExitCode"),
                "phase": r.get("phase"),
                "runStatus": r.get("runStatus"),
                "note": r.get("note"),
                **{k: r["rubric"][k] for k in items},
                "strictMeets": r["strictRubric"]["meets"],
                "structuredCommands": r["rubric"]["structuredCommands"],
            }
            for r in ok
        },
        "noBlock": sorted(
            r["case"] for r in ok if not r.get("isBlock") and not r.get("notEvaluated")
        ),
        "notEvaluated": not_evaluated,
        "rubric": {key: rate(blocks, key) for key in items},
        "strictRubric": {key: rate(blocks, key, "strictRubric") for key in items},
        "suggestedCommands": {
            "executed": len(executed),
            "usageFailures": sum(1 for s in executed if s["usageFailure"]),
            "usageFailureCommands": sorted({s["command"] for s in executed if s["usageFailure"]}),
            "notRun": {
                reason: sum(1 for s in skipped if s["skipped"] == reason)
                for reason in sorted({s["skipped"] for s in skipped})
            },
            "proseChecked": len(prose),
            "proseUnknownSubcommand": sorted(
                {s["command"] for s in prose if not s["subcommandExists"]}
            ),
        },
        "documentedExitCodes": documented_codes(),
    }
    summary["criteria"] = {
        "rubricAtLeast90Percent": {
            "value": meets,
            "met": bool(meets["n"]) and (meets["rate"] or 0) >= 0.9,
        },
        "note": "Pre-registered in PREREGISTRO.md (H44); evaluated as is over the observed block "
        "states (cases that did not block are listed in noBlock).",
    }
    return summary


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H44",
            title="clarity and actionability of blocks",
            stem="h44-block-clarity",
            cases=CASES,
            conditions_of=conditions_of,
            all_conditions=["H"],
            runner=runner,
            summarize=summarize,
        )
    )
