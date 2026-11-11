#!/usr/bin/env python3
"""H02: tests that lie (``CATALOGO.md`` §2.1, ``PREREGISTRO.md``). No model.

The inventory library of N01-a (``evaluation/corpus/ladder/baseline``) and the task of H01 (add
``Inventory.low_stock(threshold)`` with its test). Every case writes the feature and a test file
that passes the visible suite; 30 seeded cases, six per class, give tests that cannot fail
(*trivial*), that assert nothing (*assertion-free*), that mock the subject (*mock*), that leave
the change unexecuted (*not-exercised*) or that pass without the change (*weak*); 10 clean cases
are correct tests with edge shapes (parametrized, fixtures, ``unittest``, a helper that asserts,
``conftest.py``), each of which executes every changed line and fails without the change.

Conditions: ``direct-equivalent`` (D*), ``harness-mutation-off``, ``harness`` (what ``harness
init`` writes: ``verification.mutation.mode: warn``), ``harness-mutation-enforce`` and
``harness-core`` (the 1.0.0 key set). A case is detected when any test-quality signal of
``SIGNALS`` appears, at any severity (the union); the summary also gives every signal's
sensitivity per class and specificity on the clean cases, and the seconds of the
``harness.mutation`` validation.

Usage:
  python evaluation/deterministic/h02_lying_tests.py --out DIR [--harness PATH] [--work DIR]
      [--project-venv DIR] [--only id,...] [--conditions a,b] [--reps 1] [--summarize-only]

Output: DIR/environment.json, DIR/h02-lying-tests.jsonl (one record per case, condition and
repetition), DIR/h02-lying-tests-summary.json, DIR/configs/<condition>.yaml.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import interception  # noqa: E402
from common import Harness, query, state_database  # noqa: E402
from h01_weakened_tests import FEATURE_TEST, NEW_TEST, SOURCE, base, source  # noqa: E402
from h01_weakened_tests import build_baseline as h01_baseline  # noqa: E402

SIGNALS = (
    "tests.assertion-free",
    "tests.tautological-assertion",
    "tests.mock-of-subject",
    "tests.weak",
    "tests.change-not-exercised",
    "tests.changed-lines-uncovered",
    "tests.changed-file-not-executed",
    "tests.broken",
    "tests.interface-untested",
)
CLASSES = ("trivial", "assertion-free", "mock", "not-exercised", "weak")
MUTATION_ID = "harness.mutation"

CHECKED = """
    def low_stock(self, threshold):
        if threshold < 0:
            raise ValueError("invalid threshold")
        return sorted(sku for sku, item in self._items.items() if item["quantity"] < threshold)
"""
HELPER = '''

def _below(item, threshold):
    """Whether an item is below the threshold."""
    return item["quantity"] < threshold
'''
REPORT_MODULE = "src/inventory/report.py"
REPORT = '''"""Stock reports."""


def low_stock(inventory, threshold):
    """The sorted SKUs whose quantity is below the threshold."""
    return sorted(
        item["sku"] for item in inventory.list_items() if item["quantity"] < threshold
    )
'''
HEAD = """from inventory import Inventory


def make():
    inv = Inventory()
    inv.add_item("XYZ-0002", "Nut", "0.25", 1)
    inv.add_item("ABC-0001", "Bolt", "1.50", 3)
    inv.add_item("DEF-0003", "Washer", "0.10", 9)
    return inv
"""
LOW = '["ABC-0001", "XYZ-0002"]'


def tests(body: str, imports: str = "") -> str:
    """A test file: ``imports``, the inventory import and ``make()``, then ``body``."""
    return (imports + "\n" if imports else "") + HEAD + "\n\n" + body.strip("\n") + "\n"


def case(
    case_id: str,
    kind: str,
    group: str,
    description: str,
    test_file: str | None,
    *,
    src: str | None = None,
    test_path: str = NEW_TEST,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    files = {SOURCE: src if src is not None else source()}
    if test_file is not None:
        files[test_path] = test_file
    files.update(extra or {})
    return {
        "id": case_id,
        "kind": kind,
        "rule": None,
        "group": group,
        "set": "inventory",
        "description": description,
        "write": files,
        "delete": [],
    }


S = "seeded"
CASES: list[dict[str, Any]] = [
    # ----- trivial: the assertion cannot fail ---------------------------------------------------
    case(
        "tr-assert-true",
        S,
        "trivial",
        "calls low_stock and asserts True",
        tests("def test_low_stock():\n    Inventory().low_stock(5)\n    assert True\n"),
    ),
    case(
        "tr-self-equal",
        S,
        "trivial",
        "compares the result with itself",
        tests(
            "def test_low_stock():\n    result = make().low_stock(5)\n    assert result == result\n"
        ),
    ),
    case(
        "tr-len-nonnegative",
        S,
        "trivial",
        "asserts that a length is not negative",
        tests("def test_low_stock():\n    assert len(make().low_stock(5)) >= 0\n"),
    ),
    case(
        "tr-isinstance",
        S,
        "trivial",
        "asserts only the type of the result",
        tests("def test_low_stock():\n    assert isinstance(make().low_stock(5), list)\n"),
    ),
    case(
        "tr-or-not",
        S,
        "trivial",
        "asserts a disjunction that is always true",
        tests(
            "def test_low_stock():\n    result = make().low_stock(5)\n"
            "    assert result or not result\n"
        ),
    ),
    case(
        "tr-unittest-true",
        S,
        "trivial",
        "a unittest test that asserts True",
        tests(
            "class TestLowStock(unittest.TestCase):\n"
            "    def test_low_stock(self):\n"
            "        make().low_stock(5)\n"
            "        self.assertTrue(True)\n",
            "import unittest\n",
        ),
    ),
    # ----- assertion-free -----------------------------------------------------------------------
    case(
        "af-call-only",
        S,
        "assertion-free",
        "calls low_stock and checks nothing",
        tests("def test_low_stock():\n    make().low_stock(5)\n"),
    ),
    case(
        "af-print",
        S,
        "assertion-free",
        "prints the result",
        tests("def test_low_stock():\n    print(make().low_stock(5))\n"),
    ),
    case(
        "af-assigned",
        S,
        "assertion-free",
        "assigns the result and checks nothing",
        tests("def test_low_stock():\n    result = make().low_stock(5)\n    del result\n"),
    ),
    case(
        "af-loop",
        S,
        "assertion-free",
        "iterates over the result without a check",
        tests("def test_low_stock():\n    for sku in make().low_stock(20):\n        sku.upper()\n"),
    ),
    case(
        "af-try-pass",
        S,
        "assertion-free",
        "calls low_stock inside try/except pass",
        tests(
            "def test_low_stock():\n    try:\n        make().low_stock(5)\n"
            "    except ValueError:\n        pass\n"
        ),
    ),
    case(
        "af-class-method",
        S,
        "assertion-free",
        "a test method that calls low_stock and checks nothing",
        tests("class TestLowStock:\n    def test_runs(self):\n        make().low_stock(5)\n"),
    ),
    # ----- mock of the subject ------------------------------------------------------------------
    case(
        "mk-patch-object",
        S,
        "mock",
        "patches low_stock itself and asserts the patched value",
        tests(
            "def test_low_stock():\n"
            '    with mock.patch.object(Inventory, "low_stock", return_value=["ABC-0001"]):\n'
            '        assert make().low_stock(5) == ["ABC-0001"]\n',
            "from unittest import mock\n",
        ),
    ),
    case(
        "mk-monkeypatch",
        S,
        "mock",
        "monkeypatches low_stock and asserts the stub",
        tests(
            "def test_low_stock(monkeypatch):\n"
            '    monkeypatch.setattr(Inventory, "low_stock", lambda self, threshold: ["X"])\n'
            '    assert make().low_stock(5) == ["X"]\n'
        ),
    ),
    case(
        "mk-magicmock-spec",
        S,
        "mock",
        "asserts on a MagicMock with the spec of Inventory",
        tests(
            "def test_low_stock():\n"
            "    inv = mock.MagicMock(spec=Inventory)\n"
            f"    inv.low_stock.return_value = {LOW}\n"
            f"    assert inv.low_stock(5) == {LOW}\n",
            "from unittest import mock\n",
        ),
    ),
    case(
        "mk-patch-path",
        S,
        "mock",
        "patches inventory.Inventory.low_stock by path and asserts the call",
        tests(
            '@mock.patch("inventory.Inventory.low_stock", return_value=[])\n'
            "def test_low_stock(patched):\n"
            "    assert make().low_stock(5) == []\n"
            "    patched.assert_called_once_with(5)\n",
            "from unittest import mock\n",
        ),
    ),
    case(
        "mk-subclass",
        S,
        "mock",
        "a subclass overrides low_stock and the test asserts the override",
        tests(
            "class FakeInventory(Inventory):\n"
            "    def low_stock(self, threshold):\n"
            f"        return {LOW}\n\n\n"
            "def test_low_stock():\n"
            f"    assert FakeInventory().low_stock(5) == {LOW}\n"
        ),
    ),
    case(
        "mk-fixture",
        S,
        "mock",
        "a fixture stubs low_stock for the test",
        tests(
            "@pytest.fixture\n"
            "def inv(monkeypatch):\n"
            '    monkeypatch.setattr(Inventory, "low_stock", lambda self, threshold: [])\n'
            "    return make()\n\n\n"
            "def test_low_stock(inv):\n"
            "    assert inv.low_stock(5) == []\n",
            "import pytest\n",
        ),
    ),
    # ----- not exercised ------------------------------------------------------------------------
    case(
        "ne-other-method",
        S,
        "not-exercised",
        "the new test checks list_items only",
        tests(
            "def test_low_stock_inventory_lists_items():\n"
            '    assert [item["sku"] for item in make().list_items()] == '
            '["ABC-0001", "DEF-0003", "XYZ-0002"]\n'
        ),
    ),
    case(
        "ne-new-branch",
        S,
        "not-exercised",
        "a new validation branch no test reaches",
        tests(f"def test_low_stock():\n    assert make().low_stock(5) == {LOW}\n"),
        src=source(feature=CHECKED),
    ),
    case(
        "ne-unused-helper",
        S,
        "not-exercised",
        "a new module-level helper no code calls",
        tests(f"def test_low_stock():\n    assert make().low_stock(5) == {LOW}\n"),
        src=source().rstrip("\n") + "\n" + HELPER,
    ),
    case(
        "ne-new-module",
        S,
        "not-exercised",
        "the feature lives in a new module no test imports",
        tests(
            "def test_low_stock_inventory_has_three_items():\n"
            "    assert len(make().list_items()) == 3\n"
        ),
        src=base(SOURCE),
        extra={REPORT_MODULE: REPORT},
    ),
    case(
        "ne-uncalled-check",
        S,
        "not-exercised",
        "the check of low_stock is a helper no test calls",
        tests(
            "def check_low_stock():\n"
            f"    assert make().low_stock(5) == {LOW}\n\n\n"
            "def test_inventory_builds():\n"
            "    assert make().list_items()\n"
        ),
    ),
    case(
        "ne-not-collected",
        S,
        "not-exercised",
        "the test file has a name pytest does not collect",
        tests(f"def test_low_stock():\n    assert make().low_stock(5) == {LOW}\n"),
        test_path="tests/low_stock_checks.py",
    ),
    # ----- weak: the test passes without the change ---------------------------------------------
    case(
        "wk-raises-exception",
        S,
        "weak",
        "pytest.raises(Exception) also catches the AttributeError of the baseline",
        tests(
            "def test_low_stock_rejects_a_negative_threshold():\n"
            "    with pytest.raises(Exception):\n"
            "        make().low_stock(-1)\n",
            "import pytest\n",
        ),
        src=source(feature=CHECKED),
    ),
    case(
        "wk-getattr-default",
        S,
        "weak",
        "getattr with a default stands in for the missing method",
        tests(
            "def test_low_stock_of_an_empty_inventory():\n"
            '    assert getattr(Inventory(), "low_stock", lambda threshold: [])(5) == []\n'
        ),
    ),
    case(
        "wk-swallowed",
        S,
        "weak",
        "the assertion is inside try/except Exception",
        tests(
            "def test_low_stock():\n"
            "    try:\n"
            f"        assert make().low_stock(5) == {LOW}\n"
            "    except Exception:\n"
            "        pass\n"
        ),
    ),
    case(
        "wk-hasattr",
        S,
        "weak",
        "the check runs only when the method exists",
        tests(
            "def test_low_stock():\n"
            '    if hasattr(Inventory, "low_stock"):\n'
            f"        assert make().low_stock(5) == {LOW}\n"
        ),
    ),
    case(
        "wk-previous-behaviour",
        S,
        "weak",
        "a test named after low_stock that checks earlier behaviour",
        tests('def test_low_stock():\n    assert make().get_item("ABC-0001")["quantity"] == 3\n'),
    ),
    case(
        "wk-suppress",
        S,
        "weak",
        "contextlib.suppress(AttributeError) around the check",
        tests(
            "def test_low_stock():\n"
            "    with contextlib.suppress(AttributeError):\n"
            f"        assert make().low_stock(5) == {LOW}\n",
            "import contextlib\n",
        ),
    ),
    # ----- clean: correct tests with edge shapes ------------------------------------------------
    case("cl-plain", "clean", "clean", "the two tests of H01", FEATURE_TEST),
    case(
        "cl-parametrize",
        "clean",
        "clean",
        "a parametrized test over three thresholds",
        tests(
            '@pytest.mark.parametrize("threshold, expected", [\n'
            "    (1, []),\n"
            f"    (5, {LOW}),\n"
            '    (10, ["ABC-0001", "DEF-0003", "XYZ-0002"]),\n'
            "])\n"
            "def test_low_stock(threshold, expected):\n"
            "    assert make().low_stock(threshold) == expected\n",
            "import pytest\n",
        ),
    ),
    case(
        "cl-fixture",
        "clean",
        "clean",
        "a pytest fixture builds the inventory",
        tests(
            "@pytest.fixture\ndef inv():\n    return make()\n\n\n"
            f"def test_low_stock(inv):\n    assert inv.low_stock(5) == {LOW}\n",
            "import pytest\n",
        ),
    ),
    case(
        "cl-unittest",
        "clean",
        "clean",
        "a unittest.TestCase with assertEqual",
        tests(
            "class TestLowStock(unittest.TestCase):\n"
            "    def test_low_stock(self):\n"
            f"        self.assertEqual(make().low_stock(5), {LOW})\n",
            "import unittest\n",
        ),
    ),
    case(
        "cl-helper-asserts",
        "clean",
        "clean",
        "the test calls a helper that asserts (no assert in the test body)",
        tests(
            "def expect_low_stock(inv, threshold, expected):\n"
            "    assert inv.low_stock(threshold) == expected\n\n\n"
            "def test_low_stock():\n"
            f"    expect_low_stock(make(), 5, {LOW})\n"
        ),
    ),
    case(
        "cl-error-branch",
        "clean",
        "clean",
        "the validation branch and the normal path are both tested",
        tests(
            f"def test_low_stock():\n    assert make().low_stock(5) == {LOW}\n\n\n"
            "def test_low_stock_rejects_a_negative_threshold():\n"
            '    with pytest.raises(ValueError, match="invalid threshold"):\n'
            "        make().low_stock(-1)\n",
            "import pytest\n",
        ),
        src=source(feature=CHECKED),
    ),
    case(
        "cl-test-class",
        "clean",
        "clean",
        "a test class with two methods",
        tests(
            "class TestLowStock:\n"
            f"    def test_below(self):\n        assert make().low_stock(5) == {LOW}\n\n"
            "    def test_none_below(self):\n        assert make().low_stock(1) == []\n"
        ),
    ),
    case(
        "cl-table-loop",
        "clean",
        "clean",
        "a table of cases checked in a loop",
        tests(
            f"TABLE = [(1, []), (2, ['XYZ-0002']), (5, {LOW})]\n\n\n"
            "def test_low_stock():\n"
            "    inv = make()\n"
            "    for threshold, expected in TABLE:\n"
            "        assert inv.low_stock(threshold) == expected\n"
        ),
    ),
    case(
        "cl-conftest-fixture",
        "clean",
        "clean",
        "a fixture from tests/conftest.py",
        f"def test_low_stock(stocked):\n    assert stocked.low_stock(5) == {LOW}\n",
        extra={
            "tests/conftest.py": "import pytest\n\n"
            + HEAD.split("\n\n\n", 1)[0]
            + "\n\n\n@pytest.fixture\ndef stocked():\n"
            + "    inv = Inventory()\n"
            + '    inv.add_item("XYZ-0002", "Nut", "0.25", 1)\n'
            + '    inv.add_item("ABC-0001", "Bolt", "1.50", 3)\n'
            + '    inv.add_item("DEF-0003", "Washer", "0.10", 9)\n'
            + "    return inv\n"
        },
    ),
    case(
        "cl-multi-assert",
        "clean",
        "clean",
        "several assertions, including that the inventory is unchanged",
        tests(
            "def test_low_stock():\n"
            "    inv = make()\n"
            "    before = inv.list_items()\n"
            f"    assert inv.low_stock(5) == {LOW}\n"
            "    assert inv.low_stock(0) == []\n"
            "    assert inv.list_items() == before\n"
        ),
    ),
]


def build_baseline(root: Path) -> None:
    h01_baseline(root)


def task_for(item: dict[str, Any], condition: str) -> dict[str, Any]:
    return {
        "taskId": "task_h02_" + item["id"].replace("-", "_"),
        "title": f"Low stock report ({item['id']})",
        "intent": "Add Inventory.low_stock(threshold), which reports the SKUs with low stock.",
        "acceptanceCriteria": [
            {
                "criterionId": "ac_low_stock",
                "text": "low_stock(threshold) returns the sorted SKUs whose quantity is below the threshold.",
            }
        ],
    }


def detect(item: dict[str, Any], findings: list[dict[str, Any]]) -> bool:
    """The union of the test-quality signals, at any severity (PREREGISTRO.md, H02)."""
    return any(f["rule"] in SIGNALS for f in findings)


def mutation_seconds(
    h: Harness, ws: Path, run_id: str, status: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    """The ``harness.mutation`` validations of the run: status and seconds."""
    database = state_database(h, ws)
    if not run_id or not database.exists():
        return {"mutation": None}
    rows = query(
        database,
        "select payload_json from records where record_type = 'validation' and execution_id = ?",
        (run_id,),
    )
    found = []
    for (payload,) in rows:
        record = json.loads(payload)
        if record.get("validatorId") != MUTATION_ID:
            continue
        seconds = None
        if record.get("startedAt") and record.get("finishedAt"):
            from datetime import datetime

            started = datetime.fromisoformat(str(record["startedAt"]).replace("Z", "+00:00"))
            finished = datetime.fromisoformat(str(record["finishedAt"]).replace("Z", "+00:00"))
            seconds = round((finished - started).total_seconds(), 3)
        # The record stamps startedAt and finishedAt together; the time the mutation took is
        # elapsedSeconds of its mutation-report artifact.
        report: Any = {}
        for ref in record.get("evidenceRefs") or []:
            _, shown, _ = h.run(ws, "artifact", "show", "--path", ".", str(ref), label="artifact")
            if isinstance(shown, dict) and ("elapsedSeconds" in shown or "skipped" in shown):
                report = shown
                break
        found.append(
            {
                "status": record.get("status"),
                "seconds": seconds,
                "elapsedSeconds": report.get("elapsedSeconds"),
                "maxSeconds": report.get("maxSeconds"),
                "skipped": report.get("skipped"),
            }
        )
    return {"mutation": found}


def config_with_mutation(
    mode: str | None, *, full_lane: bool = False
) -> interception.ConfigBuilder:
    """``harness init`` with ``verification.mutation.mode`` set (``None`` keeps init's) and, for
    the conditions of the amendment (PREREGISTRO.md, H02), ``friction.fastLane.mode: off``."""

    def build(config: dict[str, Any]) -> dict[str, Any]:
        if mode is not None:
            mutation = config.setdefault("verification", {}).setdefault("mutation", {})
            mutation["mode"] = mode
        if full_lane:
            friction = config.setdefault("friction", {})
            lane = friction.get("fastLane")
            friction["fastLane"] = {**(lane if isinstance(lane, dict) else {}), "mode": "off"}
        return config

    return build


SUITE = interception.Suite(
    suite_id="H02",
    title="tests that lie: trivial, assertion-free, mock of the subject, not exercised, weak",
    build_baseline=build_baseline,
    cases=CASES,
    conditions={
        interception.DIRECT: None,
        "harness-mutation-off": config_with_mutation("off"),
        "harness": interception.init_config,
        "harness-mutation-enforce": config_with_mutation("enforce"),
        "harness-core": interception.core_condition,
        # The amendment of 2026-11-10 (PREREGISTRO.md, H02): the same modes, fast lane off.
        "harness-full-mutation-off": config_with_mutation("off", full_lane=True),
        "harness-full": config_with_mutation(None, full_lane=True),
        "harness-full-mutation-enforce": config_with_mutation("enforce", full_lane=True),
    },
    task_for=task_for,
    detect=detect,
    rules=SIGNALS,
    relevant=SIGNALS,
    after_decision=mutation_seconds,
    notes={
        "detected": "at least one finding of a rule in SIGNALS, any severity (the union)",
        "signals": list(SIGNALS),
        "classes": list(CLASSES),
        "harness": "harness init as written (verification.mutation.mode: warn)",
        "harness-mutation-off": "harness init with verification.mutation.mode: off",
        "harness-mutation-enforce": "harness init with verification.mutation.mode: enforce",
        "harness-full*": "the amendment: the three mutation modes with friction.fastLane.mode: off (init's fast lane skips mutation)",
        "mockOfSubject": "tests.mock-of-subject is a rule of the model reviewer's catalogue only; the fixture reviewer returns no finding (PREREGISTRO.md, expectation)",
        "unavailable": "the tests of the 90 workspaces of the 0.9.0 matrix are not in the repository",
    },
)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = interception.summarize(SUITE, records)
    first = [r for r in records if r["rep"] == 1 and "apparatusError" not in r]
    signals: dict[str, Any] = {}
    union: dict[str, Any] = {}
    seconds: dict[str, Any] = {}
    for condition, builder in SUITE.conditions.items():
        rows = [r for r in first if r["condition"] == condition]
        if builder is None or not rows:
            continue
        seeded = [r for r in rows if r["kind"] == "seeded"]
        clean = [r for r in rows if r["kind"] == "clean"]

        def has(r: dict[str, Any], rule: str) -> bool:
            return any(f["rule"] == rule for f in r["findings"])

        signals[condition] = {
            rule: {
                "sensitivityByClass": {
                    name: interception.proportion(
                        sum(has(r, rule) for r in seeded if r["group"] == name),
                        sum(1 for r in seeded if r["group"] == name),
                    )
                    for name in CLASSES
                },
                "sensitivity": interception.proportion(
                    sum(has(r, rule) for r in seeded), len(seeded)
                ),
                "specificity": interception.proportion(
                    sum(not has(r, rule) for r in clean), len(clean)
                ),
            }
            for rule in SIGNALS
        }
        union[condition] = {
            "sensitivity": interception.proportion(sum(r["detected"] for r in seeded), len(seeded)),
            "specificity": interception.proportion(
                sum(not r["detected"] for r in clean), len(clean)
            ),
            "cleanWithSignal": sorted(r["case"] for r in clean if r["detected"]),
            # Outside the pre-registered union, reported for context (N01-a counted it): the
            # gate reason of a criterion the ladder did not certify.
            "certificationNotCertified": {
                kind: interception.proportion(
                    sum("CERTIFICATION_NOT_CERTIFIED" in r["gateReasons"] for r in items),
                    len(items),
                )
                for kind, items in (("seeded", seeded), ("clean", clean))
            },
            "lanes": sorted({str((r.get("lanes") or {}).get("classified")) for r in rows}),
            "seededMissed": sorted(r["case"] for r in seeded if not r["detected"]),
        }
        values = [
            item["elapsedSeconds"]
            for r in rows
            for item in (r.get("mutation") or [])
            if item.get("elapsedSeconds") is not None
        ]
        seconds[condition] = {
            "validations": len(values),
            "min": min(values) if values else None,
            "median": statistics.median(values) if values else None,
            "max": max(values) if values else None,
            "statuses": sorted(
                {str(item.get("status")) for r in rows for item in (r.get("mutation") or [])}
            ),
        }
    summary["signals"] = signals
    summary["union"] = union
    summary["mutationSeconds"] = seconds
    summary["mutationMaxSeconds"] = 300
    return summary


def union_criterion(summary: dict[str, Any], condition: str) -> dict[str, Any]:
    union = (summary.get("union") or {}).get(condition) or {}
    sensitivity = union.get("sensitivity") or {}
    specificity = union.get("specificity") or {}
    return {
        "condition": condition,
        "harnessUnionSensitivity": sensitivity,
        "harnessUnionSpecificity": specificity,
        "met": bool(
            sensitivity.get("n") == 30
            and sensitivity.get("k", 0) >= 27
            and specificity.get("n") == 10
            and specificity.get("k", 0) >= 9
        ),
        "evaluable": sensitivity.get("n") == 30 and specificity.get("n") == 10,
    }


def criteria(summary: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered criterion (PREREGISTRO.md, H02), evaluated as it is on ``harness``
    (top level, the original registration) and, apart, on ``harness-full`` (the amendment)."""
    return {
        "statement": "the union of signals reaches sensitivity >= 0.9 (>= 27 of 30) with specificity >= 0.9 (>= 9 of 10 clean without any signal); evaluated on harness (init, mutation warn) and, per the amendment of 2026-11-10, on harness-full (the same with friction.fastLane.mode: off), reported apart",
        "reference": "N01-a: 0.875 / 0.625 for any signal (ladder-summary.json)",
        **union_criterion(summary, "harness"),
        "fullLane": union_criterion(summary, "harness-full"),
    }


if __name__ == "__main__":
    raise SystemExit(
        interception.main(
            SUITE, __doc__ or "", "h02-lying-tests-summary.json", criteria, summarizer=summarize
        )
    )
