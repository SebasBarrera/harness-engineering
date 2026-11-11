#!/usr/bin/env python3
"""H23: agent answers that break the response contract of wave 11 must fail closed. No model is
called.

Each case gives one read-only call kind of a run of the sample project (``friction.py``'s
pricing function) a malformed answer (``CASES`` below); every other call gets a valid answer
from the fixture provider (``suite_support.FIXTURE_AGENT``). The bad answer is given either on the
first call of the kind only (``once``: the contract retry of ``runtime.contractRetry: once``
should resolve it) or on every call of the kind (``always``: the retry also fails and the call
must end blocked). The waits before DECISION are answered by the declared simulated person.

Conditions (``CATALOGO.md`` H23): ``enforce`` (``harness init`` as written) and ``off``
(``agentContract.mode: off``). Factor: ``once`` / ``always``.

Per run it records, for the target kind: the attempts the fixture received, the
``agent.call.recorded`` events (``finalStatus``, ``violations``, ``receivedBytes``) and the
``agent-call-record`` evidence they point at (``summaryTruncated``, what the parent received),
the ``agent.call.contract-retry`` events, the invocations that ended in a protocol error, the
finding rules, the final status and phase. From that:

* ``detected``: a contract violation, a contract retry, a protocol error, a bounded (truncated)
  summary or a malformed-result finding of the target kind;
* ``parentBytes``: the largest response a target call returned to the parent (``enforce``: the
  ``receivedBytes`` the harness recorded; ``off`` records nothing, so it is ``null`` and the bytes
  of the bad answer are kept as an upper bound);
* ``outOfCapReachedParent``: a recorded response of the target kind that reached the parent
  with ``finalStatus: PASSED`` and still breaks the caps (checked with ``check_answer`` and the
  summary cap of the harness under test);
* ``acceptedOutOfContract``: the bad attempt's invocation ended ``PASSED`` and nothing above
  detected it (the out-of-contract answer was used).

Usage:
  python evaluation/deterministic/contract_violations.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--only-cases a,b] [--only-conditions enforce,off]
      [--repeat once,always] [--wheel WHEEL]

Writes ``DIR/contract-violations.jsonl``, ``DIR/contract-violations-summary.json`` and
``DIR/environment.json``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    Harness,
    add_common_arguments,
    environment,
    stamp,
    state_database,
    write_json,
    write_jsonl,
)
from suite_support import (  # noqa: E402
    SAMPLE_CODE,
    bin_dir,
    commit_init,
    configure,
    events,
    find_blob,
    findings,
    portable_env,
    read_log,
    sample_change,
    sample_project,
    sample_task,
    start_run,
    status_of,
    write_fixture,
)

CONDITIONS = ("enforce", "off")
REPEATS = ("once", "always")
SURVEY = {
    "style": "custom",
    "summary": "No layering.",
    "layers": [],
    "allow": {},
    "organization": "None.",
    "components": [{"name": "app", "path": ".", "responsibility": "The project."}],
    "conventions": [],
    "guidance": ["Follow the structure."],
}


def answer(kind: str, result: Any, summary: str | None = None, **extra: Any) -> str:
    value: dict[str, Any] = {"status": "PASSED", "summary": summary or kind}
    if result is not ...:
        value["result"] = result
    value.update(extra)
    return json.dumps(value)


def panel_answer(findings: list[dict[str, Any]], summary: str = "No finding.") -> str:
    """The answer of a review-panel reviewer (its outputContract)."""
    result = {"verdict": "PASS", "findings": findings, "summary": summary}
    return json.dumps({"status": "PASSED", "summary": "review", "result": result})


def nested(depth: int) -> str:
    return "[" * depth + "]" * depth


@dataclass(frozen=True)
class Case:
    name: str
    kind: str
    stdout: str
    what: str
    # The 2.0.0 design's prediction (True: detected), per condition; None: no prediction.
    predicted: dict[str, bool | None]
    why: str
    panel: bool = False


BOTH = {"enforce": True, "off": True}
ENFORCE_ONLY = {"enforce": True, "off": False}
QUESTION = {"questionId": "q1", "ruleId": "C1", "question": "Which rounding applies?"}
CASES: list[Case] = [
    Case(
        "extra-key-clarify",
        "clarify",
        answer("clarify", {"questions": [], "reasoning": "I weighed every requirement first."}),
        "A clarify result with a key outside the contract (reasoning).",
        ENFORCE_ONLY,
        "check_answer: keys outside RESULT_KEYS['clarify'].",
    ),
    Case(
        "extra-key-architecture",
        "architecture",
        answer("architecture", {**SURVEY, "notes": "Internal notes for the parent."}),
        "An architecture survey with a key outside the contract (notes).",
        ENFORCE_ONLY,
        "check_answer: keys outside RESULT_KEYS['architecture'].",
    ),
    Case(
        "result-over-cap-clarify",
        "clarify",
        answer(
            "clarify",
            {
                "questions": [
                    {**QUESTION, "questionId": f"q{i}", "question": "q" * 900} for i in range(40)
                ]
            },
        ),
        "A clarify result of about 38 kB, above the 32 768-byte cap of clarify.",
        ENFORCE_ONLY,
        "check_answer: result above config.result_bytes('clarify').",
    ),
    # harness init configures the review panel, which replaces the single reviewer of
    # review.agentReview (orchestration/agent_review.py): the review calls of a run are the
    # panel's reviewers (requests with an outputContract), so the review cases target them.
    Case(
        "result-over-cap-review",
        "review",
        panel_answer(
            [
                {
                    "severity": "LOW",
                    "ruleId": "style",
                    "message": "m" * 900,
                    "path": "src/sample/pricing.py",
                }
                for _ in range(80)
            ]
        ),
        "A panel reviewer's result of about 80 kB, above the 65 536-byte cap of review.",
        {"enforce": None, "off": None},
        "The panel's answers are read by review/contract.py, not by the agent contract's settle "
        "(orchestration/review_panel.py): observed, not predicted.",
        panel=True,
    ),
    Case(
        "file-content-review",
        "review",
        panel_answer(
            [
                {
                    "severity": "LOW",
                    "ruleId": "style",
                    "message": "See the file.",
                    "path": "src/sample/pricing.py",
                    "content": SAMPLE_CODE,
                }
            ]
        ),
        "A panel reviewer's finding that carries the content of a file.",
        {"enforce": None, "off": None},
        "As result-over-cap-review: observed.",
        panel=True,
    ),
    Case(
        "file-content-architecture",
        "architecture",
        answer(
            "architecture",
            {
                **SURVEY,
                "components": [
                    {
                        "name": "app",
                        "path": ".",
                        "responsibility": "The project.",
                        "source": SAMPLE_CODE,
                    }
                ],
            },
        ),
        "An architecture survey whose component carries source code.",
        ENFORCE_ONLY,
        "_field_problems: FILE_CONTENT_KEYS outside acceptance.",
    ),
    Case(
        "summary-over-600-clarify",
        "clarify",
        answer("clarify", {"questions": []}, summary="s" * 2000),
        "A valid clarify result with a 2 000-character summary.",
        ENFORCE_ONLY,
        "limit_summary cuts it to summaryChars (600); not a violation, a bounded summary.",
    ),
    Case(
        "field-over-4000-clarify",
        "clarify",
        answer("clarify", {"questions": [{**QUESTION, "question": "q" * 5000}]}),
        "A clarify question of 5 000 characters (field cap 4 000).",
        ENFORCE_ONLY,
        "_field_problems: a text field above fieldChars.",
    ),
    Case(
        "field-over-4000-acceptance",
        "acceptance",
        answer(
            "acceptance",
            {
                "tests": [
                    {
                        "path": "tests/acceptance/test_x.py",
                        "name": "n" * 5000,
                        "content": "def test_x() -> None:\n    assert True\n",
                    }
                ]
            },
        ),
        "An acceptance test whose name has 5 000 characters (only its content may exceed).",
        ENFORCE_ONLY,
        "_field_problems: only acceptance '.content' is exempt.",
    ),
    Case(
        "not-json-clarify",
        "clarify",
        "There are no questions about this task.",
        "A clarify answer that is not JSON.",
        BOTH,
        "Protocol error in both modes; contractRetry retries a protocol error.",
    ),
    Case(
        "no-result-clarify",
        "clarify",
        answer("clarify", ...),
        "A PASSED clarify answer without result.",
        {"enforce": None, "off": None},
        "check_answer(None) is empty: the contract has no rule for a missing result; what the "
        "intent phase does with it is observed, not predicted.",
    ),
    Case(
        "deep-nesting-clarify",
        "clarify",
        '{"status": "PASSED", "summary": "clarify", "result": {"questions": [' + nested(64) + "]}}",
        "A clarify result nested 64 lists deep inside questions.",
        {"enforce": None, "off": None},
        "Within every cap: the contract does not bound depth; the phase's own validation of a "
        "question is observed.",
    ),
    Case(
        "deep-nesting-extreme-clarify",
        "clarify",
        '{"status": "PASSED", "summary": "clarify", "result": {"questions": ['
        + nested(50000)
        + "]}}",
        "A clarify answer nested 50 000 lists deep (about 100 kB): beyond the JSON parser's "
        "recursion limit.",
        {"enforce": None, "off": None},
        "Robustness of the parse; fail closed means a protocol error, not a crash.",
    ),
    Case(
        "summary-over-600-review",
        "review",
        panel_answer([], summary="s" * 3000),
        "A panel reviewer's valid result with a 3 000-character summary.",
        {"enforce": None, "off": None},
        "As result-over-cap-review: observed.",
        panel=True,
    ),
    Case(
        "wrong-type-result-clarify",
        "clarify",
        answer("clarify", "There are no questions."),
        "A clarify answer whose result is a string, not an object.",
        BOTH,
        "Protocol error (the 1.1 protocol requires an object).",
    ),
]


# Six requirements make the task M (agentRouting thresholds 5/15 of harness init): an S task
# without risk takes the fast lane, which skips the ambiguity review (clarify), localisation and
# the agent review (lane.step.skipped, review.agent.skipped), so those kinds would never be called.
SUBTOTALS = (100, 120, 150, 200, 250, 1000)


def h23_task() -> dict[str, Any]:
    task = sample_task("task_h23_discount")
    task["requirements"] = [
        {
            "requirementId": f"req_discount_{subtotal}",
            "text": f"apply_discount({subtotal}, 100, 0.1) returns {subtotal * 0.9:g}.",
        }
        for subtotal in SUBTOTALS
    ]
    task["metadata"]["ownedPaths"].append("tests/test_trace_ids.py")
    return task


def artifact_json(run_dir: Path, uri: str) -> Any:
    path = find_blob(run_dir, uri)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def out_of_cap(kind: str, received: dict[str, Any]) -> list[str] | None:
    """What still breaks the caps in a response the parent received (``None``: unknown)."""
    try:
        from governed_harness.agents.contract import check_answer
        from governed_harness.configuration.agent_contract import AgentContractConfig
    except ImportError:
        return None
    config = AgentContractConfig()
    problems = list(check_answer(kind, received.get("result"), config))
    if len(str(received.get("summary") or "")) > config.summary_chars:
        problems.append("summary above summaryChars")
    return problems


def run_case(case: Case, condition: str, repeat: str, args: argparse.Namespace) -> dict[str, Any]:
    run_dir = args.work / f"h23-{case.name}-{condition}-{repeat}-{stamp()}"
    ws = run_dir / "ws"
    ws.mkdir(parents=True)
    sample_project(ws)
    state_root = run_dir / "harness-state"
    h = Harness(args.harness, portable_env([bin_dir(args.project_venv)], state_root))
    h.run(ws, "init", "--path", ".", label="init")
    commit_init(ws)
    log = Path(tempfile.mkdtemp(prefix="h23-")) / "calls.jsonl"
    command = write_fixture(
        run_dir,
        {
            "log": str(log),
            "change": sample_change(),
            "traceTests": {
                "path": "tests/test_trace_ids.py",
                "header": "from sample import apply_discount\n",
                "assert": "apply_discount(100, 100, 0.1) == 90",
            },
            "inject": {
                "kind": case.kind,
                "panel": case.panel,
                "repeat": repeat,
                "stdout": case.stdout,
            },
        },
    )

    def mutate(config: dict[str, Any]) -> None:
        if condition == "off":
            config.setdefault("agentContract", {})["mode"] = "off"

    config, deviations = configure(ws, command, mutate=mutate)
    database = state_database(h, ws)
    started = start_run(h, ws, run_dir, h23_task())
    run_id = started["runId"]
    status = status_of(h, ws, run_id)
    execution = status.get("execution") or {}
    run_events = events(database, run_id)
    calls = read_log(log)
    target = [c for c in calls if c["kind"] == case.kind and bool(c.get("panel")) == case.panel]
    recorded = []
    for e in run_events:
        if e["type"] != "agent.call.recorded" or e["payload"].get("callKind") != case.kind:
            continue
        evidence = artifact_json(run_dir, str(e["payload"].get("evidenceRef") or ""))
        received = (evidence or {}).get("received") or {}
        recorded.append(
            {
                "iteration": e["payload"].get("iteration"),
                "finalStatus": e["payload"].get("finalStatus"),
                "violations": e["payload"].get("violations"),
                "receivedBytes": e["payload"].get("receivedBytes"),
                "summaryTruncated": (evidence or {}).get("summaryTruncated"),
                "receivedSummaryChars": len(str(received.get("summary") or "")),
                "outOfCap": out_of_cap(case.kind, received)
                if e["payload"].get("finalStatus") == "PASSED"
                else [],
            }
        )
    retries = [
        e["payload"]
        for e in run_events
        if e["type"] == "agent.call.contract-retry" and e["payload"].get("callKind") == case.kind
    ]
    invocations = [
        e["payload"]
        for e in run_events
        if e["type"] == "agent.invocation.completed"
        and (e["payload"].get("callKind") or e["payload"].get("call_kind")) == case.kind
    ]
    protocol_errors = [
        i for i in invocations if i.get("error") or i.get("status") not in {"PASSED"}
    ]
    rules = findings(h, ws, run_id)
    kind_findings = [
        r for r in rules if any(word in r for word in ("contract", "malformed", "protocol"))
    ]
    truncated = any(r.get("summaryTruncated") for r in recorded)
    detected = bool(
        any(r["finalStatus"] == "CONTRACT_VIOLATION" for r in recorded)
        or retries
        or protocol_errors
        or truncated
        or kind_findings
    )
    first_status = invocations[0].get("status") if invocations else None
    known = [r["outOfCap"] for r in recorded if r["outOfCap"] is not None]
    record: dict[str, Any] = {
        "case": case.name,
        "kind": case.kind,
        "panel": case.panel,
        "condition": condition,
        "repeat": repeat,
        "what": case.what,
        "predictedDetected": case.predicted[condition],
        "why": case.why,
        "deviations": deviations,
        "contractRetry": (config.get("runtime") or {}).get("contractRetry"),
        "runExitCode": started["runExitCode"],
        "waits": started["waits"],
        "finalStatus": execution.get("status"),
        "finalPhase": execution.get("currentPhase"),
        "lane": next(
            (e["payload"].get("lane") for e in run_events if e["type"] == "lane.classified"), None
        ),
        "callsByKind": sorted({c["kind"] + (":panel" if c.get("panel") else "") for c in calls}),
        "targetAttempts": len(target),
        "injectedAttempts": sum(1 for c in target if c.get("injected")),
        "injectedAnswerBytes": len(case.stdout.encode("utf-8")),
        "recorded": recorded,
        "contractRetries": len(retries),
        "retryProblems": [str(r.get("problem"))[:300] for r in retries],
        "targetInvocations": [
            {
                "status": i.get("status"),
                "error": (i.get("error") or {}).get("errorType")
                if isinstance(i.get("error"), dict)
                else i.get("error"),
            }
            for i in invocations
        ],
        "findingRules": rules,
        "detected": detected,
        "matchesPrediction": None
        if case.predicted[condition] is None
        else detected == case.predicted[condition],
        "retryResolved": bool(retries)
        and bool(recorded)
        and recorded[-1]["finalStatus"] == "PASSED"
        if condition == "enforce"
        else (bool(retries) and bool(invocations) and invocations[-1].get("status") == "PASSED"),
        "parentBytes": max((r["receivedBytes"] or 0 for r in recorded), default=None)
        if recorded
        else None,
        "outOfCapReachedParent": any(bool(p) for p in known)
        if known
        else (False if recorded else None),
        "acceptedOutOfContract": first_status == "PASSED" and not detected,
        "steps": list(h.steps),
    }
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    for condition in CONDITIONS:
        for repeat in REPEATS:
            rows = [r for r in records if r["condition"] == condition and r["repeat"] == repeat]
            if not rows:
                continue
            cells[f"{condition}/{repeat}"] = {
                "cases": len(rows),
                "detected": sum(r["detected"] for r in rows),
                "notDetected": sorted(r["case"] for r in rows if not r["detected"]),
                "acceptedOutOfContract": sorted(
                    r["case"] for r in rows if r["acceptedOutOfContract"]
                ),
                "outOfCapReachedParent": sorted(
                    r["case"] for r in rows if r["outOfCapReachedParent"]
                ),
                "retryResolved": sorted(r["case"] for r in rows if r["retryResolved"]),
                "contractRetries": {r["case"]: r["contractRetries"] for r in rows},
                "maxParentBytes": max((r["parentBytes"] or 0 for r in rows), default=None),
                "differentFromPrediction": sorted(
                    r["case"] for r in rows if r["matchesPrediction"] is False
                ),
                "final": {
                    r["case"]: f"{r['finalStatus']}/{r['finalPhase']}/exit {r['runExitCode']}"
                    for r in rows
                },
            }
    return {
        "detected": "contract violation, contract retry, protocol error, bounded summary or "
        "malformed-result finding of the target kind",
        "byCell": cells,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--project-venv", type=Path, default=Path(sys.executable).parent.parent)
    parser.add_argument("--only-cases", default="")
    parser.add_argument("--only-conditions", default="")
    parser.add_argument("--repeat", default=",".join(REPEATS))
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.work = (args.work or args.out / ".work").resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    records_path = args.out / "contract-violations.jsonl"
    if not args.summarize_only:
        cases = [c for c in CASES if not args.only_cases or c.name in args.only_cases.split(",")]
        conditions = args.only_conditions.split(",") if args.only_conditions else list(CONDITIONS)
        repeats = [r for r in args.repeat.split(",") if r]
        write_json(
            args.out / "environment.json",
            environment(
                args.harness,
                args.wheel,
                {
                    "suite": "H23 contract violations (fixture provider)",
                    "cases": [c.name for c in cases],
                    "conditions": conditions,
                    "repeat": repeats,
                    "projectVenv": str(args.project_venv),
                },
            ),
        )
        records = []
        for condition in conditions:
            for repeat in repeats:
                for case in cases:
                    record = run_case(case, condition, repeat, args)
                    records.append(record)
                    print(
                        condition,
                        repeat,
                        case.name,
                        "detected" if record["detected"] else "NOT detected",
                        record["contractRetries"],
                        record["parentBytes"],
                        record["finalStatus"],
                        record["finalPhase"],
                        flush=True,
                    )
        write_jsonl(records_path, records)
    records = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    write_json(args.out / "contract-violations-summary.json", summarize(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
