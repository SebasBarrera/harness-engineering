#!/usr/bin/env python3
"""H31: TDD red-green (``CATALOGO.md`` §2.6, ``PREREGISTRO.md``). No model is called.

Twenty changes on the sample project (``suite_support.sample_project``: the threshold discount,
which returns the subtotal on the baseline). Every change writes the correct discount and one
test: ten tests "written after" pass on the baseline already, ten red-first tests fail there and
pass with the change. The same test reaches the harness by its two channels: the fixture
proposes it as an acceptance test (format pytest; the simulated person approves it, the harness
freezes it and runs it before the change) and writes it in ``implement`` as the change's own test
(``tests/test_h31_own.py``), with the code.

Conditions: ``harness-tdd`` (``testing.strategy: tdd``), ``harness-conventional``
(``testing.strategy: conventional``) and ``direct-equivalent`` (D*: the test on the baseline and
on the change, which is also the truth of the corpus; it is not an agent). Per run: the
``acceptance.passes-before`` and ``tdd.not-red`` findings, the acceptance ``failBefore`` status,
the TDD evidence (red, green, refactor) and the gate.

Usage:
  python evaluation/deterministic/h31_tdd_red_green.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only A01,R01]
      [--conditions harness-tdd,harness-conventional,direct-equivalent] [--reps 1]
      [--summarize-only] [--keep-work]

Writes ``DIR/h31-tdd-red-green.jsonl``, ``DIR/h31-tdd-red-green-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from practice_support import flag, records_of, workspace, write_files  # noqa: E402
from resource_support import (  # noqa: E402
    CORRECT_CODE,
    cleanup,
    environment_python,
    finding_items,
    main,
    proportion,
    run_state,
    saved_config,
    stamp_dir,
)
from suite_support import bin_dir, find_blob, portable_env, sample_project, start_run  # noqa: E402

TDD = "harness-tdd"
CONVENTIONAL = "harness-conventional"
DIRECT = "direct-equivalent"
OWN_TEST = "tests/test_h31_own.py"
ACCEPTANCE_TEST = "{directory}/test_h31_acceptance.py"
HEADER = "from sample import apply_discount\n"
PYTEST = "import pytest\n\n"
NEW_FUNCTION = (
    "\n\ndef discount_amount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal - apply_discount(subtotal, threshold, rate)\n"
)

# (id, kind, description, test body, needs pytest, adds discount_amount)
_CORPUS: list[tuple[str, str, str, str, bool, bool]] = [
    ("A01", "after", "below the threshold", "assert apply_discount(99, 100, 0.1) == 99", False, False),
    ("A02", "after", "zero subtotal", "assert apply_discount(0, 100, 0.1) == 0", False, False),
    ("A03", "after", "zero rate at the threshold", "assert apply_discount(100, 100, 0.0) == 100", False, False),
    ("A04", "after", "tautology", "assert True", False, False),
    ("A05", "after", "type of the result", "assert isinstance(apply_discount(50, 100, 0.1), (int, float))", False, False),
    ("A06", "after", "parametrized below the threshold", "", True, False),
    ("A07", "after", "the function exists", "assert callable(apply_discount)", False, False),
    ("A08", "after", "an inequality the baseline meets", "assert apply_discount(10, 100, 0.5) <= 10", False, False),
    ("A09", "after", "far below the threshold", "assert apply_discount(1, 1000, 0.9) == 1", False, False),
    ("A10", "after", "a call without assertion", "apply_discount(100, 100, 0.1)", False, False),
    ("R01", "red-first", "at the threshold", "assert apply_discount(100, 100, 0.1) == 90", False, False),
    ("R02", "red-first", "above the threshold", "assert apply_discount(150, 100, 0.1) == 135", False, False),
    ("R03", "red-first", "another rate", "assert apply_discount(200, 100, 0.2) == 160", False, False),
    ("R04", "red-first", "parametrized above the threshold", "", True, False),
    ("R05", "red-first", "pytest.approx", "assert apply_discount(100.5, 100, 0.1) == pytest.approx(90.45)", True, False),
    ("R06", "red-first", "a function the baseline lacks", "", True, True),
    ("R07", "red-first", "a test class", "", False, False),
    ("R08", "red-first", "two assertions", "assert apply_discount(99, 100, 0.1) == 99\n    assert apply_discount(100, 100, 0.1) == 90", False, False),
    ("R09", "red-first", "rounding", "assert round(apply_discount(110, 100, 0.1), 2) == 99.0", False, False),
    ("R10", "red-first", "a pytest fixture", "", True, False),
]  # fmt: skip
SPECIAL = {
    "A06": '@pytest.mark.parametrize("subtotal", [1, 50, 99])\n'
    "def test_case(subtotal: int) -> None:\n"
    "    assert apply_discount(subtotal, 100, 0.1) == subtotal\n",
    "R04": '@pytest.mark.parametrize(("subtotal", "total"), [(100, 90), (200, 180), (300, 270)])\n'
    "def test_case(subtotal: int, total: int) -> None:\n"
    "    assert apply_discount(subtotal, 100, 0.1) == total\n",
    "R06": "def test_case() -> None:\n"
    "    from sample import discount_amount\n\n"
    "    assert discount_amount(100, 100, 0.1) == pytest.approx(10)\n",
    "R07": "class TestDiscount:\n"
    "    def test_case(self) -> None:\n"
    "        assert apply_discount(120, 100, 0.25) == 90\n",
    "R10": "@pytest.fixture\n"
    "def rate() -> float:\n    return 0.1\n\n\n"
    "def test_case(rate: float) -> None:\n"
    "    assert apply_discount(100, 100, rate) == 90\n",
}


def test_text(case_id: str, body: str, needs_pytest: bool) -> str:
    code = SPECIAL.get(case_id) or f"def test_case() -> None:\n    {body}\n"
    return (PYTEST if needs_pytest else "") + HEADER + "\n\n" + code


CASES = [
    {
        "id": case_id,
        "kind": kind,
        "description": description,
        "test": test_text(case_id, body, needs_pytest),
        "newFunction": new_function,
    }
    for case_id, kind, description, body, needs_pytest, new_function in _CORPUS
]


def code_files(case: dict[str, Any]) -> dict[str, str]:
    files = {"src/sample/pricing.py": CORRECT_CODE + (NEW_FUNCTION if case["newFunction"] else "")}
    if case["newFunction"]:
        files["src/sample/__init__.py"] = "from .pricing import apply_discount, discount_amount\n"
    return files


# ----- D* --------------------------------------------------------------------------------------
def pytest_in(ws: Path, args: argparse.Namespace, env: dict[str, str], test: str) -> int:
    try:
        return subprocess.run(
            [environment_python(args), "-m", "pytest", "-q", "-p", "no:cacheprovider", test],
            cwd=ws,
            env=env,
            capture_output=True,
            timeout=600,
        ).returncode
    except subprocess.TimeoutExpired:
        return -9


def direct(case: dict[str, Any], args: argparse.Namespace, rep: int) -> dict[str, Any]:
    run_dir = stamp_dir(args, "h31", case["id"], DIRECT, f"r{rep}")
    ws = run_dir / "ws"
    ws.mkdir()
    sample_project(ws)
    env = portable_env([bin_dir(args.project_venv)], run_dir / "state")
    write_files(ws, {OWN_TEST: case["test"]})
    before = pytest_in(ws, args, env, OWN_TEST)
    write_files(ws, code_files(case))
    after = pytest_in(ws, args, env, OWN_TEST)
    try:
        suite = subprocess.run(
            [environment_python(args), "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=ws,
            env=env,
            capture_output=True,
            timeout=600,
        ).returncode
    except subprocess.TimeoutExpired:
        suite = -9
    cleanup(run_dir, args.keep_work)
    observed = "after" if before == 0 and after == 0 else "red-first" if after == 0 else "other"
    return {
        "kind": case["kind"],
        "testBeforeExitCode": before,
        "testAfterExitCode": after,
        "passesBefore": before == 0,
        "passesAfter": after == 0,
        "observedKind": observed,
        "labelMatches": observed == case["kind"],
        "testsExitCode": suite,
        "delivered": suite == 0,
        "brief": f"before={before} after={after} suite={suite}",
    }


# ----- governed --------------------------------------------------------------------------------
def tdd_evidence(run_dir: Path, database: Path, run_id: str) -> list[dict[str, Any]]:
    """The run's ``tdd-evidence`` records (red, green, refactor), read from their artifacts."""
    found = []
    for evidence in records_of(database, run_id, "evidence"):
        summary = str(evidence.get("summary") or "")
        if not summary.startswith("TDD:"):
            continue
        ref = str(evidence.get("artifactRef") or evidence.get("artifact_ref") or "")
        blob = find_blob(run_dir, ref)
        value: dict[str, Any] = {}
        if blob is not None:
            try:
                value = json.loads(blob.read_bytes())
            except ValueError:
                value = {}
        red = value.get("red")
        found.append(
            {
                "summary": summary,
                "red": red.get("status") if isinstance(red, dict) else red,
                "green": (value.get("green") or {}).get("status"),
                "refactor": (value.get("refactor") or {}).get("status"),
                "tests": value.get("tests"),
            }
        )
    return found


def governed(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    run_dir = stamp_dir(args, "h31", case["id"], condition, f"r{rep}")
    strategy = "tdd" if condition == TDD else "conventional"

    def mutate(config: dict[str, Any]) -> None:
        config.setdefault("testing", {})["strategy"] = strategy

    spec = {
        "acceptance": {"pytest": [{"path": ACCEPTANCE_TEST, "content": case["test"]}]},
        "write": {**code_files(case), OWN_TEST: case["test"]},
    }
    item = workspace(run_dir, args, sample_project, spec, mutate=mutate)
    saved_config(args, condition, item)
    owned = sorted({*code_files(case), OWN_TEST, "tests/test_pricing.py"})
    task = {
        "taskId": f"task_h31_{case['id'].lower()}",
        "title": "Threshold discount",
        "intent": "Apply the discount only when the subtotal meets the threshold.",
        "requirements": [
            {"requirementId": "req_threshold", "text": "apply_discount(100, 100, 0.1) returns 90."}
        ],
        "acceptanceCriteria": [
            {"criterionId": "ac_threshold", "text": "apply_discount(100, 100, 0.1) returns 90."}
        ],
        "metadata": {"ownedPaths": owned},
    }
    started = start_run(item.h, item.ws, run_dir, task)
    run_id = started["runId"]
    state = run_state(item, run_id)
    found = finding_items(item.h, item.ws, run_id)
    rules = sorted({f"{f['severity']}:{f['rule']}" for f in found})
    acceptance = flag(item.database, f"acceptance:{run_id}") or {}
    evidence = tdd_evidence(run_dir, item.database, run_id)
    record = {
        "kind": case["kind"],
        "strategy": strategy,
        "runId": run_id,
        "runExitCode": started["runExitCode"],
        "waits": started["waits"],
        **state,
        "findings": rules,
        "acceptancePassesBefore": any(f["rule"] == "acceptance.passes-before" for f in found),
        "tddNotRed": any(f["rule"] == "tdd.not-red" for f in found),
        "tddRules": sorted({f["rule"] for f in found if str(f["rule"]).startswith("tdd.")}),
        "acceptanceStatus": acceptance.get("status"),
        "acceptanceFrozen": sorted(acceptance.get("frozen") or {}),
        "failBefore": (acceptance.get("failBefore") or {}).get("status"),
        "tddEvidence": evidence,
        "deviations": item.deviations,
        "steps": item.h.steps,
    }
    record["brief"] = (
        f"passesBefore={record['acceptancePassesBefore']} notRed={record['tddNotRed']} "
        f"failBefore={record['failBefore']} tdd={[e['summary'] for e in evidence]}"
    )
    cleanup(run_dir, args.keep_work)
    return record


def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    if condition == DIRECT:
        return direct(case, args, rep)
    return governed(case, condition, args, rep)


# ----- summary ---------------------------------------------------------------------------------
def marked(rows: list[dict[str, Any]], kind: str, signal: str) -> dict[str, Any]:
    items = [r for r in rows if r["kind"] == kind]
    result = proportion(sum(bool(r.get(signal)) for r in items), len(items))
    result["cases"] = sorted(r["case"] for r in items if r.get(signal))
    return result


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    direct_rows = [r for r in ok if r["condition"] == DIRECT]
    by_condition: dict[str, Any] = {
        DIRECT: {
            "delivered": proportion(sum(r["delivered"] for r in direct_rows), len(direct_rows)),
            "corpusLabelMismatches": sorted(
                r["case"] for r in direct_rows if not r["labelMatches"]
            ),
        }
    }
    for condition in (TDD, CONVENTIONAL):
        rows = [r for r in ok if r["condition"] == condition]
        if not rows:
            continue
        evidence = [e for r in rows for e in r["tddEvidence"]]
        by_condition[condition] = {
            "runs": len(rows),
            "acceptancePassesBefore": {
                "after": marked(rows, "after", "acceptancePassesBefore"),
                "redFirst": marked(rows, "red-first", "acceptancePassesBefore"),
            },
            "tddNotRed": {
                "after": marked(rows, "after", "tddNotRed"),
                "redFirst": marked(rows, "red-first", "tddNotRed"),
            },
            "failBefore": {r["case"]: r["failBefore"] for r in rows},
            "runsWithTddEvidence": proportion(sum(bool(r["tddEvidence"]) for r in rows), len(rows)),
            "tddEvidence": {r["case"]: [e["summary"] for e in r["tddEvidence"]] for r in rows},
            "redStatuses": sorted({str(e["red"]) for e in evidence}),
            "gates": {r["case"]: f"{r.get('phase')}:{r.get('gate')}" for r in rows},
            "tddRules": sorted({rule for r in rows for rule in r["tddRules"]}),
        }
    criteria: dict[str, Any] = {}
    tdd = by_condition.get(TDD)
    for signal in ("acceptancePassesBefore", "tddNotRed"):
        after = tdd[signal]["after"] if tdd else {"k": 0, "n": 0}
        red = tdd[signal]["redFirst"] if tdd else {"k": 0, "n": 0}
        criteria[signal] = {
            "value": f"after marked {after['k']}/{after['n']}, red-first marked "
            f"{red['k']}/{red['n']} (harness-tdd)",
            "met": after["n"] == 10 and after["k"] == 10 and red["n"] == 10 and red["k"] == 0,
        }
    criteria["bothSignals"] = {
        "met": criteria["acceptancePassesBefore"]["met"] and criteria["tddNotRed"]["met"]
    }
    criteria["note"] = (
        "Pre-registered in PREREGISTRO.md (H31): 100 % of the tests that pass before marked and "
        "0 red-first marked, for each signal, under harness-tdd. Reported as is; a criterion "
        "with missing runs is not met."
    )
    return {
        "suite": "H31",
        "title": "TDD red-green",
        "directEquivalent": "D* runs the test on the baseline and on the change; it is the "
        "truth of the corpus and not an agent",
        "simulatedPerson": "human.reviewer-simulated approves the proposed acceptance tests and "
        "answers the other waits before DECISION (a declared simulation)",
        "cases": {
            kind: sum(1 for c in CASES if c["kind"] == kind) for kind in ("after", "red-first")
        },
        "byCondition": by_condition,
        "criteria": criteria,
    }


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H31",
            title="TDD red-green",
            stem="h31-tdd-red-green",
            cases=CASES,
            conditions_of=lambda case: [DIRECT, TDD, CONVENTIONAL],
            all_conditions=[DIRECT, TDD, CONVENTIONAL],
            runner=runner,
            summarize=summarize,
        )
    )
