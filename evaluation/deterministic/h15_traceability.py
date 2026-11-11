#!/usr/bin/env python3
"""H15, deterministic part: requirement -> test -> decision traceability with known truth
(``CATALOGO.md`` §2.2, ``PREREGISTRO.md``). No model.

The catalogue's test is post hoc over the ``direct`` and ``harness`` runs of block A
(``evaluation/trace_posthoc.py --runs``); those runs do not exist yet. This part validates the
instrument and measures what the harness lets through with identical changes: 10 cases on the
inventory library, one task with four requirements (``A1``-``A4``) and four criteria, the same
feature in every case and tests that name the ids in different ways (names, docstrings,
``pytest.param`` ids, comments, half of them, none, near-ids that do not count, criterion ids
instead of requirement ids, lower-case ids in the text, no new tests). Each case declares which
ids a test names (the truth).

Conditions: ``direct-equivalent`` (D*: the change and the visible tests), ``harness``
(``harness init``) and ``harness-core`` (the 1.0.0 key set). In the governed conditions the
declared simulated person decides in DECISION: ``APPROVE`` when the gate is ``PASSED``, otherwise
no decision. ``trace_posthoc.analyse`` measures every final workspace with the same code.

Usage:
  python evaluation/deterministic/h15_traceability.py --out DIR [--harness PATH] [--work DIR]
      [--project-venv DIR] [--only id,...] [--conditions a,b] [--reps 1] [--summarize-only]

Output: DIR/environment.json, DIR/h15-traceability.jsonl (one record per case, condition and
repetition), DIR/h15-traceability-summary.json, DIR/configs/<condition>.yaml.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import interception  # noqa: E402
import trace_posthoc  # noqa: E402
from common import SIMULATED_PERSON, Harness, query, state_database  # noqa: E402
from h01_weakened_tests import NEW_TEST, SOURCE, source  # noqa: E402
from h01_weakened_tests import build_baseline as h01_baseline  # noqa: E402
from harness_state import State, trace_completeness  # noqa: E402

REQUIREMENTS = {
    "A1": "low_stock(threshold) returns the sorted SKUs whose quantity is below the threshold.",
    "A2": "low_stock of an empty inventory is an empty list.",
    "A3": "low_stock rejects a negative threshold with ValueError.",
    "A4": "low_stock does not change the inventory.",
}
CRITERIA = {
    "ac_sorted": "low_stock(5) of three items returns the two SKUs below 5, sorted.",
    "ac_empty": "low_stock(5) of an empty inventory returns [].",
    "ac_negative": "low_stock(-1) raises ValueError.",
    "ac_unchanged": "low_stock leaves the items as they were.",
}
FEATURE = """
    def low_stock(self, threshold):
        if threshold < 0:
            raise ValueError("invalid threshold")
        return sorted(sku for sku, item in self._items.items() if item["quantity"] < threshold)
"""
HEAD = """import pytest

from inventory import Inventory


def make():
    inv = Inventory()
    inv.add_item("XYZ-0002", "Nut", "0.25", 1)
    inv.add_item("ABC-0001", "Bolt", "1.50", 3)
    inv.add_item("DEF-0003", "Washer", "0.10", 9)
    return inv
"""
BODIES = {
    "A1": '    assert make().low_stock(5) == ["ABC-0001", "XYZ-0002"]\n',
    "A2": "    assert Inventory().low_stock(5) == []\n",
    "A3": "    with pytest.raises(ValueError):\n        make().low_stock(-1)\n",
    "A4": "    inv = make()\n    before = inv.list_items()\n    inv.low_stock(5)\n"
    "    assert inv.list_items() == before\n",
}
PLAIN = {"A1": "sorted", "A2": "empty", "A3": "negative", "A4": "unchanged"}
IDS = list(REQUIREMENTS)
PLURAL = {"requirement": "requirements", "criterion": "criteria"}


def tests_file(functions: list[str]) -> str:
    return HEAD + "".join("\n\n" + f.rstrip("\n") + "\n" for f in functions)


def named_tests(name: dict[str, str] | None = None, prelude: dict[str, str] | None = None) -> str:
    """One test per requirement; ``name`` gives the function name, ``prelude`` lines that go
    before the body (docstring, comment)."""
    name, prelude = name or {}, prelude or {}
    return tests_file(
        [f"def {name.get(i, 'test_' + PLAIN[i])}():\n{prelude.get(i, '')}{BODIES[i]}" for i in IDS]
    )


PARAM = (
    '@pytest.mark.parametrize("inv, expected", [\n'
    '    pytest.param(make(), ["ABC-0001", "XYZ-0002"], id="A1"),\n'
    '    pytest.param(Inventory(), [], id="A2"),\n'
    "])\n"
    "def test_low_stock(inv, expected):\n"
    "    assert inv.low_stock(5) == expected\n"
)


def case(
    case_id: str,
    description: str,
    tests: str | None,
    requirements: list[str],
    criteria: list[str] | None = None,
) -> dict[str, Any]:
    files = {SOURCE: source(feature=FEATURE)}
    if tests is not None:
        files[NEW_TEST] = tests
    return {
        "id": case_id,
        "kind": "seeded" if set(requirements) != set(IDS) else "clean",
        "rule": None,
        "group": f"{len(requirements)}-of-4",
        "set": "inventory",
        "description": description,
        "write": files,
        "delete": [],
        "truth": {"requirements": sorted(requirements), "criteria": sorted(criteria or [])},
    }


CASES: list[dict[str, Any]] = [
    case(
        "ids-in-names",
        "every test is named after its requirement (test_a1_sorted)",
        named_tests(name={i: f"test_{i.lower()}_{PLAIN[i]}" for i in IDS}),
        IDS,
    ),
    case(
        "ids-in-docstrings",
        "every test names its requirement in its docstring",
        named_tests(prelude={i: f'    """{i}. {REQUIREMENTS[i]}"""\n' for i in IDS}),
        IDS,
    ),
    case(
        "ids-param-and-comments",
        "pytest.param ids for A1 and A2, comments for A3 and A4",
        tests_file(
            [
                PARAM,
                f"def test_negative():\n    # covers A3\n{BODIES['A3']}",
                f"def test_unchanged():\n    # covers A4\n{BODIES['A4']}",
            ]
        ),
        IDS,
    ),
    case(
        "ids-in-comments",
        "a comment with the id in every test",
        named_tests(prelude={i: f"    # {i}\n" for i in IDS}),
        IDS,
    ),
    case(
        "half-named",
        "only A1 and A2 are named",
        named_tests(name={"A1": "test_a1_sorted", "A2": "test_a2_empty"}),
        ["A1", "A2"],
    ),
    case("none-named", "good tests that name no id", named_tests(), []),
    case(
        "near-ids",
        "near-ids that do not count: A12, A1x, A2.1, testa3, A40",
        named_tests(
            name={"A1": "test_a12_sorted", "A3": "testa3_negative"},
            prelude={
                "A2": '    """A1x and A2.1 are near-ids."""\n',
                "A4": "    # see A40\n",
            },
        ),
        [],
    ),
    case(
        "criterion-ids",
        "tests named after the criteria, not the requirements",
        named_tests(
            name={
                "A1": "test_ac_sorted",
                "A2": "test_ac_empty",
                "A3": "test_ac_negative",
                "A4": "test_ac_unchanged",
            }
        ),
        [],
        list(CRITERIA),
    ),
    case(
        "lower-case-text",
        "the ids in lower case in comments (the text match is case-sensitive)",
        named_tests(prelude={i: f"    # covers {i.lower()}\n" for i in IDS}),
        [],
    ),
    case("no-new-tests", "the feature without new tests", None, []),
]


def build_baseline(root: Path) -> None:
    h01_baseline(root)


def task_for(item: dict[str, Any], condition: str) -> dict[str, Any]:
    return {
        "taskId": "task_h15_" + item["id"].replace("-", "_"),
        "title": f"Low stock report ({item['id']})",
        "intent": "Add Inventory.low_stock(threshold), which reports the SKUs with low stock.",
        "requirements": [{"text": f"{i}. {text}"} for i, text in REQUIREMENTS.items()],
        "acceptanceCriteria": [{"criterionId": c, "text": t} for c, t in CRITERIA.items()],
    }


TASK = task_for({"id": "posthoc"}, "")


def detect(item: dict[str, Any], findings: list[dict[str, Any]]) -> bool:
    return any(f["rule"] == "traceability.requirement-untested" for f in findings)


def measured(ws: Path, item: dict[str, Any]) -> dict[str, Any]:
    """The post-hoc measure of the final workspace, and its agreement with the case's truth."""
    report = trace_posthoc.analyse(ws, TASK)
    truth = item["truth"]
    agreement = {
        i["id"]: i["named"] == (i["id"] in truth[PLURAL[i["kind"]]]) for i in report["ids"]
    }
    return {"posthoc": report, "posthocAgreesWithTruth": agreement}


def direct_measure(ws: Path, item: dict[str, Any]) -> dict[str, Any]:
    return measured(ws, item)


UNTESTED = re.compile(r"No test names requirement (\S+?):")


def decide_and_measure(
    h: Harness, ws: Path, run_id: str, status: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    execution = status.get("execution") or {}
    gate = status.get("gate") or {}
    record: dict[str, Any] = {}
    if execution.get("currentPhase") == "DECISION" and gate.get("status") == "PASSED":
        code, payload, _ = h.run(
            ws,
            "gate",
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--decision",
            "APPROVE",
            "--change-set-digest",
            str(execution.get("changeSetDigest")),
            "--actor",
            SIMULATED_PERSON,
            "--rationale",
            "simulated approval: the gate passed",
            label="gate decide APPROVE",
        )
        record["approveExitCode"] = code
        record["approveError"] = (
            str(payload.get("error"))[-400:]
            if isinstance(payload, dict) and payload.get("error")
            else None
        )
    else:
        record["approveExitCode"] = "not at DECISION with the gate PASSED"
    record["deliveredByHarness"] = record["approveExitCode"] == 0
    _, final, _ = h.run(ws, "status", "--path", ".", "--run", run_id, label="status final")
    final = final if isinstance(final, dict) else {}
    record["finalPhase"] = (final.get("execution") or {}).get("currentPhase")
    database = state_database(h, ws)
    record["decision"] = trace_posthoc.decision_bound(database, run_id)
    if database.exists() and final.get("execution"):
        state = State(database)
        try:
            record["traceCompleteness"] = trace_completeness(state, final)
        except (KeyError, TypeError) as error:
            record["traceCompleteness"] = {"error": repr(error)}
        finally:
            state.close()
    _, listed, _ = h.run(ws, "findings", "list", "--path", ".", "--run", run_id, label="findings")
    untraced = sorted(
        {
            match.group(1)
            for f in (listed if isinstance(listed, list) else [])
            if isinstance(f, dict) and f.get("ruleId") == "traceability.requirement-untested"
            for match in [UNTESTED.search(str(f.get("message") or ""))]
            if match
        }
    )
    record["harnessUntraced"] = untraced
    record.update(measured(ws, item))
    named = {i["id"] for i in record["posthoc"]["ids"] if i["kind"] == "requirement" and i["named"]}
    checked = database.exists() and bool(
        query(
            database,
            "select 1 from records where record_type = 'validation' and execution_id = ? "
            "and payload_json like '%traceability.requirements%'",
            (run_id,),
        )
    )
    record["harnessCheckedTraceability"] = checked
    record["posthocAgreesWithHarness"] = (
        {i: (i in named) == (i not in untraced) for i in IDS} if checked else None
    )
    return record


SUITE = interception.Suite(
    suite_id="H15",
    title="requirement -> test -> decision traceability (deterministic part)",
    build_baseline=build_baseline,
    cases=CASES,
    conditions={
        interception.DIRECT: None,
        "harness": interception.init_config,
        "harness-core": interception.core_condition,
    },
    task_for=task_for,
    detect=detect,
    rules=("traceability.requirement-untested",),
    relevant=("traceability.",),
    after_decision=decide_and_measure,
    after_direct=direct_measure,
    notes={
        "kind": "clean = a test names every requirement; seeded = some requirement is not named",
        "decision": "the simulated person approves in DECISION when the gate PASSED (declared, PREREGISTRO.md H15)",
        "instrument": "evaluation/trace_posthoc.py, independent of the harness package",
        "blockA": "the catalogue's criterion is evaluated by trace_posthoc.py --runs on block A runs",
    },
)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = interception.summarize(SUITE, records)
    first = [r for r in records if r["rep"] == 1 and "apparatusError" not in r]
    by: dict[str, Any] = {}
    for condition, builder in SUITE.conditions.items():
        rows = [r for r in first if r["condition"] == condition and r.get("posthoc")]
        if not rows:
            continue

        def delivered(r: dict[str, Any], direct: bool = builder is None) -> bool:
            return bool(r.get("delivered") if direct else r.get("deliveredByHarness"))

        def rate(items: list[dict[str, Any]], kind: str) -> dict[str, Any]:
            named = sum(r["posthoc"][kind]["named"] for r in items)
            total = sum(r["posthoc"][kind]["total"] for r in items)
            return interception.proportion(named, total)

        shipped = [r for r in rows if delivered(r)]
        agreement = [v for r in rows for v in r["posthocAgreesWithTruth"].values()]
        harness_agreement = [
            v for r in rows for v in (r.get("posthocAgreesWithHarness") or {}).values()
        ]
        by[condition] = {
            "requirementsNamed": rate(rows, "requirements"),
            "criteriaNamed": rate(rows, "criteria"),
            "delivered": interception.proportion(len(shipped), len(rows)),
            "requirementsNamedInDelivered": rate(shipped, "requirements"),
            "notDelivered": sorted(r["case"] for r in rows if not delivered(r)),
            "decisionBound": (
                None
                if builder is None
                else interception.proportion(
                    sum(bool((r.get("decision") or {}).get("bound")) for r in rows), len(rows)
                )
            ),
            "traceCompletenessPresent": (
                None
                if builder is None
                else {
                    r["case"]: [
                        (r.get("traceCompleteness") or {}).get("present"),
                        (r.get("traceCompleteness") or {}).get("requiredCount"),
                        (r.get("traceCompleteness") or {}).get("present2"),
                        (r.get("traceCompleteness") or {}).get("requiredCount2"),
                    ]
                    for r in rows
                }
            ),
            "posthocAccuracyVsTruth": interception.proportion(sum(agreement), len(agreement)),
            "posthocAgreementWithHarness": interception.proportion(
                sum(harness_agreement), len(harness_agreement)
            ),
            "perCase": {r["case"]: r["posthoc"]["requirements"]["rate"] for r in rows},
        }
    summary["traceability"] = by
    return summary


def criteria(summary: dict[str, Any]) -> dict[str, Any]:
    """The catalogue's criterion needs block A records (PREREGISTRO.md, H15)."""
    by = summary.get("traceability") or {}
    return {
        "statement": "the fraction of requirements with a named test is reported per condition; the hypothesis holds if harness >= direct in every scenario x model cell of block A",
        "evaluable": False,
        "met": None,
        "reason": "only evaluable on block A runs (evaluation/trace_posthoc.py --runs); this deterministic part reports its metrics without a threshold",
        "deterministic": {
            condition: {
                "requirementsNamed": item["requirementsNamed"],
                "requirementsNamedInDelivered": item["requirementsNamedInDelivered"],
                "delivered": item["delivered"],
            }
            for condition, item in by.items()
        },
    }


if __name__ == "__main__":
    raise SystemExit(
        interception.main(
            SUITE, __doc__ or "", "h15-traceability-summary.json", criteria, summarizer=summarize
        )
    )
