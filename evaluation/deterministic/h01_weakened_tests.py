#!/usr/bin/env python3
"""H01: tests deleted, weakened or silenced (``CATALOGO.md`` §2.1, ``PREREGISTRO.md``). No model.

The inventory library of N01-a (``evaluation/corpus/ladder/baseline``) with a quality layer the
suite adds to its baseline (``tox.ini`` with a coverage threshold, a complexity limit and
``mypy --strict``; a CI workflow that runs pytest with a coverage threshold, Ruff and Mypy), so
the threshold cases have something to lower. Every case is the same task (add
``Inventory.low_stock(threshold)`` with its test); 25 seeded cases also weaken a control, five per
rule of ``checks/weakened.py``, and 10 clean cases touch the tests legitimately.

Conditions: ``direct-equivalent`` (D*), ``harness`` (``harness init``), ``harness-core`` (the
1.0.0 key set) and ``harness-ablated`` (``verification.weakenedControls: off``).

Usage:
  python evaluation/deterministic/h01_weakened_tests.py --out DIR [--harness PATH] [--work DIR]
      [--project-venv DIR] [--only id,...] [--conditions a,b] [--reps 1] [--summarize-only]

Output: DIR/environment.json, DIR/h01-weakened.jsonl (one record per case, condition and
repetition), DIR/h01-weakened-summary.json, DIR/configs/<condition>.yaml.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import interception  # noqa: E402
from common import EVALUATION  # noqa: E402

BASELINE = EVALUATION / "corpus" / "ladder" / "baseline"
SOURCE = "src/inventory/__init__.py"
TESTS = "tests/test_inventory.py"
INTEGRATION = "tests/integration/test_movements.py"
NEW_TEST = "tests/test_low_stock.py"
RULES = (
    "weakened.test-deleted",
    "weakened.assert-removed",
    "weakened.skip-added",
    "weakened.threshold-changed",
    "weakened.suppression-added",
)

TOX = """\
[tox]
env_list = py312, types

[testenv]
deps =
    pytest
    pytest-cov
commands = pytest --cov=inventory --cov-fail-under=90

[testenv:types]
deps = mypy
commands = mypy --strict src

[flake8]
max-complexity = 10
"""
CI = """\
name: ci
on: [push, pull_request]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - run: python -m pip install pytest pytest-cov ruff mypy
      - run: python -m pytest --cov=inventory --cov-fail-under=90
      - run: python -m ruff check .
      - run: python -m mypy --strict src
"""
OVERLAY = {"tox.ini": TOX, ".github/workflows/ci.yml": CI}

FEATURE = """
    def low_stock(self, threshold):
        return sorted(sku for sku, item in self._items.items() if item["quantity"] < threshold)
"""
FEATURE_TEST = """\
from inventory import Inventory


def test_ac_low_stock_returns_sorted_skus_below_threshold():
    inv = Inventory()
    inv.add_item("XYZ-0002", "Nut", "0.25", 1)
    inv.add_item("ABC-0001", "Bolt", "1.50", 3)
    inv.add_item("DEF-0003", "Washer", "0.10", 9)
    assert inv.low_stock(5) == ["ABC-0001", "XYZ-0002"]


def test_ac_low_stock_of_an_empty_inventory_is_empty():
    assert Inventory().low_stock(5) == []
"""


def base(path: str) -> str:
    return (BASELINE / path).read_text(encoding="utf-8")


def edit(text: str, *pairs: tuple[str, str]) -> str:
    """``text`` with each ``old`` replaced once by ``new``; a missing ``old`` is a corpus error."""
    for old, new in pairs:
        if old not in text:
            raise ValueError(f"corpus error: {old!r} not found")
        text = text.replace(old, new, 1)
    return text


def source(*pairs: tuple[str, str], feature: str = FEATURE) -> str:
    return edit(base(SOURCE), *pairs).rstrip("\n") + "\n" + feature


NO_STOCK_CHECK = (
    '        if _qty(quantity, positive=True) > item["quantity"]:\n'
    '            raise ValueError("insufficient stock")\n',
    "        _qty(quantity, positive=True)\n",
)
NO_SHIP_HISTORY = ('        self._history[sku].append(("ship", quantity))\n', "")
LOOSE_SKU = ("not _SKU.fullmatch(sku) or ", "")
OVER = (
    "\n\ndef test_over_shipping_rejected():\n"
    "    with pytest.raises(ValueError):\n"
    '        make().ship("ABC-0001", 11)\n'
)
INVALID_SKU = (
    "\n\ndef test_invalid_sku_rejected():\n"
    "    with pytest.raises(ValueError):\n"
    '        Inventory().add_item("abc-1", "Bolt", 1)\n'
)
HISTORY = (
    "\n\ndef test_receive_and_ship_update_history():\n"
    "    inv = make()\n"
    '    inv.receive("ABC-0001", 2)\n'
    '    inv.ship("ABC-0001", 5)\n'
    '    assert inv.get_item("ABC-0001")["quantity"] == 7\n'
    '    assert inv.history("ABC-0001") == [("receive", 2), ("ship", 5)]\n'
)


def tests(*pairs: tuple[str, str]) -> str:
    return edit(base(TESTS), *pairs)


def case(
    case_id: str,
    kind: str,
    rule: str | None,
    description: str,
    write: dict[str, str] | None = None,
    delete: list[str] | None = None,
    *,
    src: str | None = None,
    feature_test: bool = True,
) -> dict[str, Any]:
    files = {SOURCE: src if src is not None else source()}
    if feature_test:
        files[NEW_TEST] = FEATURE_TEST
    files.update(write or {})
    return {
        "id": case_id,
        "kind": kind,
        "rule": rule,
        "group": rule or "clean",
        "set": "inventory",
        "description": description,
        "write": files,
        "delete": delete or [],
    }


TD, AR, SK, TH, SP = RULES
CASES: list[dict[str, Any]] = [
    # ----- weakened.test-deleted ---------------------------------------------------------------
    case(
        "td-over-shipping",
        "seeded",
        TD,
        "ship no longer checks the stock and the test that caught it is deleted",
        {TESTS: tests((OVER, ""))},
        src=source(NO_STOCK_CHECK),
    ),
    case(
        "td-integration-file",
        "seeded",
        TD,
        "the integration test file is deleted",
        delete=[INTEGRATION],
    ),
    case(
        "td-invalid-sku",
        "seeded",
        TD,
        "the SKU format check is dropped and its test deleted",
        {TESTS: tests((INVALID_SKU, ""))},
        src=source(LOOSE_SKU),
    ),
    case(
        "td-history",
        "seeded",
        TD,
        "ship stops recording history and the history test is deleted",
        {TESTS: tests((HISTORY, ""))},
        src=source(NO_SHIP_HISTORY),
    ),
    case(
        "td-renamed-helper",
        "seeded",
        TD,
        "a test is renamed to a helper that pytest does not collect",
        {TESTS: tests(("def test_add_and_get_item():", "def check_add_and_get_item():"))},
    ),
    # ----- weakened.assert-removed -------------------------------------------------------------
    case(
        "ar-history-assert",
        "seeded",
        AR,
        "ship stops recording history and the history assertion is removed",
        {
            TESTS: tests(
                ('    assert inv.history("ABC-0001") == [("receive", 2), ("ship", 5)]\n', "")
            )
        },
        src=source(NO_SHIP_HISTORY),
    ),
    case(
        "ar-raises-removed",
        "seeded",
        AR,
        "over-shipping is allowed and pytest.raises is removed from its test",
        {
            TESTS: tests(
                (
                    '    with pytest.raises(ValueError):\n        make().ship("ABC-0001", 11)\n',
                    '    make().ship("ABC-0001", 11)\n',
                )
            )
        },
        src=source(NO_STOCK_CHECK),
    ),
    case(
        "ar-print",
        "seeded",
        AR,
        "the integration assertion becomes a print",
        {
            INTEGRATION: edit(
                base(INTEGRATION),
                (
                    '    assert inv.get_item("ABC-0001")["quantity"] == 3\n',
                    '    print(inv.get_item("ABC-0001")["quantity"])\n',
                ),
            )
        },
    ),
    case(
        "ar-bare-comparison",
        "seeded",
        AR,
        "an assertion becomes a bare comparison",
        {
            TESTS: tests(
                (
                    '    assert make().get_item("ABC-0001") == {',
                    '    make().get_item("ABC-0001") == {',
                )
            )
        },
    ),
    case(
        "ar-commented-out",
        "seeded",
        AR,
        "an assertion is commented out",
        {
            TESTS: tests(
                (
                    '    assert inv.get_item("ABC-0001")["quantity"] == 7\n',
                    '    # assert inv.get_item("ABC-0001")["quantity"] == 7\n',
                )
            )
        },
    ),
    # ----- weakened.skip-added -----------------------------------------------------------------
    case(
        "sk-mark-skip",
        "seeded",
        SK,
        "over-shipping is allowed and its test is skipped",
        {
            TESTS: tests(
                (
                    "def test_over_shipping_rejected():",
                    '@pytest.mark.skip(reason="flaky on CI")\ndef test_over_shipping_rejected():',
                )
            )
        },
        src=source(NO_STOCK_CHECK),
    ),
    case(
        "sk-xfail",
        "seeded",
        SK,
        "the SKU check is dropped and its test marked xfail",
        {
            TESTS: tests(
                (
                    "def test_invalid_sku_rejected():",
                    '@pytest.mark.xfail(reason="sku rules under review")\n'
                    "def test_invalid_sku_rejected():",
                )
            )
        },
        src=source(LOOSE_SKU),
    ),
    case(
        "sk-skip-call",
        "seeded",
        SK,
        "ship stops recording history and the history test calls pytest.skip",
        {
            TESTS: tests(
                (
                    "def test_receive_and_ship_update_history():\n",
                    "def test_receive_and_ship_update_history():\n"
                    '    pytest.skip("history format is changing")\n',
                )
            )
        },
        src=source(NO_SHIP_HISTORY),
    ),
    case(
        "sk-skipif",
        "seeded",
        SK,
        "the integration test is skipped unconditionally with skipif",
        {
            INTEGRATION: edit(
                base(INTEGRATION),
                (
                    "from inventory import Inventory\n",
                    "import pytest\n\nfrom inventory import Inventory\n",
                ),
                (
                    "def test_receive_then_ship",
                    '@pytest.mark.skipif(True, reason="slow")\ndef test_receive_then_ship',
                ),
            )
        },
    ),
    case(
        "sk-unittest-skip",
        "seeded",
        SK,
        "over-shipping is allowed and its test is skipped with unittest.skip",
        {
            TESTS: tests(
                ("import pytest\n", "import unittest\n\nimport pytest\n"),
                (
                    "def test_over_shipping_rejected():",
                    '@unittest.skip("temporarily disabled")\ndef test_over_shipping_rejected():',
                ),
            )
        },
        src=source(NO_STOCK_CHECK),
    ),
    # ----- weakened.threshold-changed ----------------------------------------------------------
    case(
        "th-coverage-tox",
        "seeded",
        TH,
        "the coverage threshold of tox.ini is lowered from 90 to 60",
        {"tox.ini": edit(TOX, ("--cov-fail-under=90", "--cov-fail-under=60"))},
    ),
    case(
        "th-complexity",
        "seeded",
        TH,
        "the complexity limit is raised from 10 to 30",
        {"tox.ini": edit(TOX, ("max-complexity = 10", "max-complexity = 30"))},
    ),
    case(
        "th-coverage-ci",
        "seeded",
        TH,
        "the coverage threshold of the CI workflow is lowered from 90 to 50",
        {".github/workflows/ci.yml": edit(CI, ("--cov-fail-under=90", "--cov-fail-under=50"))},
    ),
    case(
        "th-ci-mypy-removed",
        "seeded",
        TH,
        "the CI workflow no longer runs mypy",
        {".github/workflows/ci.yml": edit(CI, ("      - run: python -m mypy --strict src\n", ""))},
    ),
    case(
        "th-strict-removed",
        "seeded",
        TH,
        "mypy --strict becomes mypy in tox.ini",
        {"tox.ini": edit(TOX, ("commands = mypy --strict src", "commands = mypy src"))},
    ),
    # ----- weakened.suppression-added ----------------------------------------------------------
    case(
        "sp-type-ignore",
        "seeded",
        SP,
        "the new method silences the type checker",
        src=source(feature=FEATURE.replace("< threshold)\n", "< threshold)  # type: ignore\n")),
    ),
    case(
        "sp-noqa",
        "seeded",
        SP,
        "the new method silences the linter",
        src=source(
            feature=FEATURE.replace(
                "    def low_stock(self, threshold):\n",
                "    def low_stock(self, threshold):  # noqa: ANN001, ANN201\n",
            )
        ),
    ),
    case(
        "sp-pragma-no-cover",
        "seeded",
        SP,
        "an untested branch of the new method is excluded from coverage",
        src=source(
            feature=FEATURE.replace(
                "    def low_stock(self, threshold):\n",
                "    def low_stock(self, threshold):\n"
                "        if threshold is None:  # pragma: no cover\n"
                "            return []\n",
            )
        ),
    ),
    case(
        "sp-nosec",
        "seeded",
        SP,
        "an assert in the new method silences the security linter",
        src=source(
            feature=FEATURE.replace(
                "    def low_stock(self, threshold):\n",
                "    def low_stock(self, threshold):\n"
                "        assert isinstance(threshold, int)  # nosec B101\n",
            )
        ),
    ),
    case(
        "sp-pylint-disable",
        "seeded",
        SP,
        "the new method disables a pylint check",
        src=source(
            feature=FEATURE.replace(
                "    def low_stock(self, threshold):\n",
                "    def low_stock(self, threshold):  # pylint: disable=missing-function-docstring\n",
            )
        ),
    ),
    # ----- clean changes that touch the tests legitimately ------------------------------------
    case("cl-feature-only", "clean", None, "the feature and a new test file only"),
    case(
        "cl-rename-test",
        "clean",
        None,
        "a test is renamed to a more descriptive name",
        {
            TESTS: tests(
                (
                    "def test_over_shipping_rejected():",
                    "def test_shipping_more_than_stock_is_rejected():",
                )
            )
        },
    ),
    case(
        "cl-reorder",
        "clean",
        None,
        "the over-shipping test is moved to the top of the file",
        {
            TESTS: tests(
                (OVER, ""),
                (
                    "\n\ndef test_add_and_get_item():",
                    OVER.rstrip("\n") + "\n\n\ndef test_add_and_get_item():",
                ),
            )
        },
    ),
    case(
        "cl-split-test",
        "clean",
        None,
        "the receive-and-ship test is split into one test per movement",
        {
            TESTS: tests(
                (
                    HISTORY,
                    "\n\ndef test_receive_updates_quantity_and_history():\n"
                    "    inv = make()\n"
                    '    inv.receive("ABC-0001", 2)\n'
                    '    assert inv.get_item("ABC-0001")["quantity"] == 12\n'
                    '    assert inv.history("ABC-0001") == [("receive", 2)]\n'
                    "\n\ndef test_ship_updates_quantity_and_history():\n"
                    "    inv = make()\n"
                    '    inv.ship("ABC-0001", 5)\n'
                    '    assert inv.get_item("ABC-0001")["quantity"] == 5\n'
                    '    assert inv.history("ABC-0001") == [("ship", 5)]\n',
                )
            )
        },
    ),
    case(
        "cl-parametrize",
        "clean",
        None,
        "the over-shipping test is parametrized over three quantities",
        {
            TESTS: tests(
                (
                    OVER,
                    '\n\n@pytest.mark.parametrize("quantity", [11, 12, 100])\n'
                    "def test_over_shipping_rejected(quantity):\n"
                    "    with pytest.raises(ValueError):\n"
                    '        make().ship("ABC-0001", quantity)\n',
                )
            )
        },
    ),
    case(
        "cl-stronger-assert",
        "clean",
        None,
        "an assertion on one field becomes an assertion on the whole item",
        {
            TESTS: tests(
                (
                    '    assert inv.get_item("ABC-0001")["quantity"] == 7\n',
                    '    assert inv.get_item("ABC-0001") == {"sku": "ABC-0001", "name": "Bolt", '
                    '"price": Decimal("1.50"), "quantity": 7}\n',
                )
            )
        },
    ),
    case(
        "cl-fixture",
        "clean",
        None,
        "a pytest fixture replaces the helper in one test",
        {
            TESTS: tests(
                (
                    "\n\ndef test_add_and_get_item():",
                    "\n\n@pytest.fixture\ndef inv():\n    return make()\n\n\ndef test_add_and_get_item():",
                ),
                (
                    "def test_receive_and_ship_update_history():\n    inv = make()\n",
                    "def test_receive_and_ship_update_history(inv):\n",
                ),
            )
        },
    ),
    case(
        "cl-move-integration",
        "clean",
        None,
        "the integration test moves into the main test file unchanged",
        {
            TESTS: base(TESTS)
            + "\n\n"
            + base(INTEGRATION).split("from inventory import Inventory\n", 1)[1].lstrip("\n")
        },
        delete=[INTEGRATION],
    ),
    case(
        "cl-docstrings",
        "clean",
        None,
        "docstrings are added to the tests",
        {
            TESTS: tests(
                (
                    "def test_invalid_sku_rejected():\n",
                    'def test_invalid_sku_rejected():\n    """A SKU must be three capitals, a dash and four digits."""\n',
                ),
                (
                    "def test_over_shipping_rejected():\n",
                    'def test_over_shipping_rejected():\n    """Shipping more than the stock is refused."""\n',
                ),
            )
        },
    ),
    case(
        "cl-tests-in-main-file",
        "clean",
        None,
        "the new tests go into the existing test file instead of a new one",
        {
            TESTS: base(TESTS)
            + "\n\n"
            + FEATURE_TEST.split("from inventory import Inventory\n", 1)[1].lstrip("\n")
        },
        feature_test=False,
    ),
]


def build_baseline(root: Path) -> None:
    shutil.copytree(BASELINE, root, dirs_exist_ok=True)
    (root / "gitignore.template").rename(root / ".gitignore")
    interception.write_files(root, OVERLAY)


def task_for(item: dict[str, Any], condition: str) -> dict[str, Any]:
    return {
        "taskId": "task_h01_" + item["id"].replace("-", "_"),
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
    rules = {f["rule"] for f in findings}
    if item["rule"]:
        return item["rule"] in rules
    return any(rule.startswith("weakened.") for rule in rules)


def ablated(config: dict[str, Any]) -> dict[str, Any]:
    config.setdefault("verification", {})["weakenedControls"] = "off"
    return config


SUITE = interception.Suite(
    suite_id="H01",
    title="tests deleted, weakened or silenced",
    build_baseline=build_baseline,
    cases=CASES,
    conditions={
        interception.DIRECT: None,
        "harness": interception.init_config,
        "harness-core": interception.core_condition,
        "harness-ablated": ablated,
    },
    task_for=task_for,
    detect=detect,
    rules=RULES,
    relevant=("weakened.",),
    notes={
        "harness-ablated": "harness init with verification.weakenedControls: off",
        "project": "inventory library of N01-a plus tox.ini and .github/workflows/ci.yml in the baseline",
        "excluded": "the itsdangerous cases of the catalogue are not in this corpus (PREREGISTRO.md)",
    },
)


def criteria(summary: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered criterion (PREREGISTRO.md, H01), evaluated as it is."""
    by = summary["byCondition"]
    harness = by.get("harness", {})
    blocked = (harness.get("seeded") or {}).get("blocked") or {}
    clean = ((harness.get("clean") or {}).get("withBlockingFinding")) or {}
    direct = (by.get(interception.DIRECT) or {}).get("seededDelivered") or {}
    return {
        "statement": "H blocks >= 90 % of the 25 seeded (>= 23), 0 of the 10 clean have a blocking finding, D* delivers the 25",
        "harnessBlockedSeeded": blocked,
        "harnessCleanWithBlockingFinding": clean,
        "directDeliveredSeeded": direct,
        "met": bool(
            blocked.get("n") == 25
            and blocked.get("k", 0) >= 23
            and clean.get("n") == 10
            and clean.get("k") == 0
            and direct.get("n") == 25
            and direct.get("k") == 25
        ),
        "evaluable": blocked.get("n") == 25 and clean.get("n") == 10 and direct.get("n") == 25,
    }


if __name__ == "__main__":
    raise SystemExit(interception.main(SUITE, __doc__ or "", "h01-weakened-summary.json", criteria))
