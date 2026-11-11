#!/usr/bin/env python3
"""H36: principles DRY, KISS, YAGNI, boy scout and introduced debt (``CATALOGO.md`` §2.6,
``PREREGISTRO.md``). No model.

A small ``shop`` package written by the suite (``pricing.invoice_total``, ``orders.validate_order``,
the class chain ``Base -> Product -> Physical -> Perishable`` in ``models.py``, ``report.py``, its
tests and a README; a few baseline lines are deliberately untidy so that the reformat cases have
something to tidy). Every case makes the task's change (``loyalty_points`` in ``pricing.py`` with
``tests/test_feature.py``) and adds:

* 20 seeded cases, by expected rule: ``principles.dry.duplication`` (5),
  ``principles.kiss.nesting`` (4), ``architecture.function-lines`` (2) and
  ``architecture.complexity`` (1), ``principles.yagni.unused-public`` (4),
  ``principles.composition.inheritance`` (4); the new symbols are named by a test, except in
  YAGNI;
* 10 pure reformats: whitespace or layout only, 5 in a file the task does not touch (expected
  ``principles.boy-scout.reformat-only``, ``MEDIUM``) and 5 inside ``pricing.py``, the file the
  task touches (allowed cleanup).

Conditions: ``direct-equivalent`` (D*: the change and the project's tests; not an agent),
``harness`` (``harness init`` as written: ``verification.principles.mode: enforce``) and
``harness-principles-off`` (``verification.principles.mode: off``). For every case the Ruff
findings the change introduces (``ruffIntroduced``: ``ruff check --isolated`` on the baseline and
on the change, as a multiset of (code, path)) are measured once, with D*: the change is the same
under every condition.

Usage:
  python evaluation/deterministic/h36_principles.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only dry-new-module,...]
      [--conditions harness,...] [--reps 1] [--summarize-only]

Writes ``DIR/h36-principles.jsonl``, ``DIR/h36-principles-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import interception  # noqa: E402
from common import stamp  # noqa: E402
from resource_support import cleanup, main, proportion  # noqa: E402

PRICING = "src/shop/pricing.py"
DEBT_TEST = "tests/test_debt.py"
PRINCIPLE_RULES = (
    "principles.dry.duplication",
    "principles.kiss.nesting",
    "principles.yagni.unused-public",
    "principles.composition.inheritance",
    "principles.boy-scout.reformat-only",
    "principles.kiss.module-size",
)
SIZE_RULES = ("architecture.function-lines", "architecture.complexity")
REFORMAT = "principles.boy-scout.reformat-only"

# ----- the project -----------------------------------------------------------------------------
INVOICE_BODY = (
    "    subtotal = 0.0\n"
    "    for line in lines:\n"
    '        quantity = int(line["quantity"])\n'
    '        unit_price = float(line["unit_price"])\n'
    "        if quantity <= 0:\n"
    '            raise ValueError("quantity must be positive")\n'
    "        subtotal += quantity * unit_price\n"
    "    taxes = round(subtotal*tax_rate, 2)\n"
    "    return round(subtotal + taxes, 2)\n"
)
UNIT_LABEL = (
    "def unit_label(code):\n"
    '    return "-".join(part.strip().upper() for part in code.split("-") if part)\n'
)
UNIT_LABEL_SPLIT = (
    "def unit_label(code):\n"
    '    return "-".join(\n'
    '        part.strip().upper() for part in code.split("-") if part\n'
    "    )\n"
)
PRICING_BASE = (
    '"""Prices and invoices."""\n\n\n'
    "def invoice_total(lines, tax_rate):   \n" + INVOICE_BODY + "\n" + UNIT_LABEL
)
VALIDATE_BODY = (
    '    if not order.get("id"):\n'
    '        raise ValueError("order id is required")\n'
    '    if not order.get("customer"):\n'
    '        raise ValueError("customer is required")\n'
    '    if not order.get("lines"):\n'
    '        raise ValueError("an order needs lines")\n'
    "    return True\n"
)
ORDERS_BASE = '"""Orders."""\n\n\ndef validate_order(order):  \n' + VALIDATE_BODY
MODELS_BASE = (
    '"""Catalogue models."""\n\n'
    "class Base:\n    def describe(self):\n        return type(self).__name__\n\n"
    "class Product(Base):\n    pass\n\n"
    "class Physical(Product):\n    weight = 1.0\n\n"
    "class Perishable(Physical):\n    days = 7\n"
)
REPORT_BASE = (
    '"""Text reports."""\n\n\n'
    "def summary_line(name, total):\n"
    '    return f"{name}: {total:.2f}"\n\n\n'
    "def header(title):\n"
    "    width=len(title)+4\n"
    '    return "=" * width + "\\n" + title.upper()\n\n\n'
    "def table(rows):\n"
    '    return "\\n".join(summary_line(name, total) for name, total in rows if total)\n'
)
README_BASE = "# shop\n\nA small shop package with prices, orders and\nreports.\n"
BASE_TESTS = """\
import pytest

from shop.models import Perishable
from shop.orders import validate_order
from shop.pricing import invoice_total, unit_label
from shop.report import header, summary_line, table


def test_invoice_total():
    assert invoice_total([{"quantity": 2, "unit_price": 5}], 0.1) == 11.0


def test_invoice_rejects_zero_quantity():
    with pytest.raises(ValueError):
        invoice_total([{"quantity": 0, "unit_price": 5}], 0.1)


def test_unit_label():
    assert unit_label(" kg-box ") == "KG-BOX"


def test_validate_order():
    assert validate_order({"id": 1, "customer": "ada", "lines": [1]})


def test_models():
    assert Perishable().describe() == "Perishable"


def test_report():
    assert summary_line("a", 1) == "a: 1.00"
    assert header("ab").startswith("======")
    assert table([("a", 1)]) == "a: 1.00"
"""
FEATURE = "\n\ndef loyalty_points(total):\n    return int(total // 10)\n"
FEATURE_TEST = """\
from shop.pricing import loyalty_points


def test_ac_feature_loyalty_points():
    assert loyalty_points(125) == 12
"""


def build_baseline(root: Path) -> None:
    interception.write_files(
        root,
        {
            "src/shop/__init__.py": "",
            PRICING: PRICING_BASE,
            "src/shop/orders.py": ORDERS_BASE,
            "src/shop/models.py": MODELS_BASE,
            "src/shop/report.py": REPORT_BASE,
            "tests/test_shop.py": BASE_TESTS,
            "README.md": README_BASE,
            "pyproject.toml": (
                '[project]\nname = "shop"\nversion = "0.1.0"\n\n'
                '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
            ),
            ".gitignore": ".harness/\n__pycache__/\n.pytest_cache/\n.coverage*\n",
        },
    )


def pricing(*, base: str = PRICING_BASE, extra: str = "") -> str:
    """``pricing.py`` with the task's change (and ``extra`` code after it)."""
    return base + FEATURE + extra


def test_debt(imports: str, body: str) -> str:
    return f"{imports}\n\n\ndef test_ac_feature_debt():\n{body}"


def case(
    case_id: str,
    kind: str,
    group: str,
    rule: str | None,
    description: str,
    files: dict[str, str],
    *,
    pricing_text: str | None = None,
) -> dict[str, Any]:
    write = {PRICING: pricing_text or pricing(), "tests/test_feature.py": FEATURE_TEST}
    write.update(files)
    return {
        "id": case_id,
        "kind": kind,
        "group": group,
        "rule": rule,
        "description": description,
        "write": write,
    }


def indented(text: str, prefix: str) -> str:
    return "".join(prefix + line if line.strip() else line for line in text.splitlines(True))


INVOICE_ARGS = '    assert {name}([{{"quantity": 2, "unit_price": 5}}], 0.1) == 11.0\n'
DRY = "principles.dry.duplication"
NEST = "principles.kiss.nesting"
YAGNI = "principles.yagni.unused-public"
INHERIT = "principles.composition.inheritance"

SEEDED = [
    # DRY
    case(
        "dry-new-module",
        "seeded",
        "dry",
        DRY,
        "invoice_total's body copied into a new module",
        {
            "src/shop/invoices.py": "def invoice_total_copy(lines, tax_rate):\n" + INVOICE_BODY,
            DEBT_TEST: test_debt(
                "from shop.invoices import invoice_total_copy",
                INVOICE_ARGS.format(name="invoice_total_copy"),
            ),
        },
    ),
    case(
        "dry-same-module",
        "seeded",
        "dry",
        DRY,
        "invoice_total's body copied into the same module",
        {
            DEBT_TEST: test_debt(
                "from shop.pricing import invoice_total_v2",
                INVOICE_ARGS.format(name="invoice_total_v2"),
            )
        },
        pricing_text=pricing(extra="\n\ndef invoice_total_v2(lines, tax_rate):\n" + INVOICE_BODY),
    ),
    case(
        "dry-blank-and-comments",
        "seeded",
        "dry",
        DRY,
        "invoice_total's body copied with blank lines and comments in between",
        {
            "src/shop/billing.py": "def bill(lines, tax_rate):\n"
            + "".join(
                f"{line}    # step {index}\n\n"
                for index, line in enumerate(INVOICE_BODY.splitlines(), start=1)
            ).replace("    # step", "\n    # step"),
            DEBT_TEST: test_debt("from shop.billing import bill", INVOICE_ARGS.format(name="bill")),
        },
    ),
    case(
        "dry-other-indentation",
        "seeded",
        "dry",
        DRY,
        "invoice_total's body copied into a method (other indentation)",
        {
            "src/shop/cart.py": "class Cart:\n    def total(self, lines, tax_rate):\n"
            + indented(INVOICE_BODY, "    "),
            DEBT_TEST: test_debt(
                "from shop.cart import Cart",
                '    assert Cart().total([{"quantity": 2, "unit_price": 5}], 0.1) == 11.0\n',
            ),
        },
    ),
    case(
        "dry-validate-copy",
        "seeded",
        "dry",
        DRY,
        "validate_order's body copied into a new module",
        {
            "src/shop/checkout.py": "def check_order(order):\n" + VALIDATE_BODY,
            DEBT_TEST: test_debt(
                "from shop.checkout import check_order",
                '    assert check_order({"id": 1, "customer": "ada", "lines": [1]})\n',
            ),
        },
    ),
    # KISS nesting
    case(
        "nest-ifs",
        "seeded",
        "nesting",
        NEST,
        "six nested ifs (a line 7 levels deep)",
        {
            "src/shop/rules.py": (
                "def deep_rule(a, b, c, d, e, f):\n"
                "    if a:\n        if b:\n            if c:\n                if d:\n"
                "                    if e:\n                        if f:\n"
                '                            return "all"\n'
                '    return "some"\n'
            ),
            DEBT_TEST: test_debt(
                "from shop.rules import deep_rule",
                '    assert deep_rule(1, 1, 1, 1, 1, 1) == "all"\n'
                '    assert deep_rule(0, 1, 1, 1, 1, 1) == "some"\n',
            ),
        },
    ),
    case(
        "nest-loops",
        "seeded",
        "nesting",
        NEST,
        "for, if and while nested (a line 7 levels deep)",
        {
            "src/shop/pairs.py": (
                "def count_pairs(matrix):\n"
                "    total = 0\n"
                "    for row in matrix:\n"
                "        for cell in row:\n"
                "            if cell:\n"
                "                for item in cell:\n"
                "                    if item > 0:\n"
                "                        while item > 10:\n"
                "                            item -= 10\n"
                "                        total += item\n"
                "    return total\n"
            ),
            DEBT_TEST: test_debt(
                "from shop.pairs import count_pairs", "    assert count_pairs([[[3, 12]]]) == 5\n"
            ),
        },
    ),
    case(
        "nest-with-try",
        "seeded",
        "nesting",
        NEST,
        "for, try, with, for and ifs nested (a line 7 levels deep)",
        {
            "src/shop/reader.py": (
                "def read_first(paths):\n"
                "    for path in paths:\n"
                "        try:\n"
                '            with open(path, encoding="utf-8") as handle:\n'
                "                for line in handle:\n"
                "                    if line.strip():\n"
                '                        if not line.startswith("#"):\n'
                "                            return line.strip()\n"
                "        except OSError:\n"
                "            continue\n"
                "    return None\n"
            ),
            DEBT_TEST: test_debt(
                "from shop.reader import read_first",
                '    assert read_first(["missing-file.txt"]) is None\n',
            ),
        },
    ),
    case(
        "nest-method",
        "seeded",
        "nesting",
        NEST,
        "nested blocks inside a method (a line 7 levels deep)",
        {
            "src/shop/grid.py": (
                "class Grid:\n"
                "    def find(self, rows, target):\n"
                "        for row in rows:\n"
                "            for col in row:\n"
                "                if col:\n"
                "                    if col == target:\n"
                "                        if target:\n"
                "                            return True\n"
                "        return False\n"
            ),
            DEBT_TEST: test_debt(
                "from shop.grid import Grid", "    assert Grid().find([[1, 2]], 2) is True\n"
            ),
        },
    ),
]


def long_function(name: str, lines: int) -> str:
    """A function of exactly ``lines`` lines (def, steps, return)."""
    steps = "".join(f"    step_{index:03d} = {index}\n" for index in range(lines - 2))
    return f"def {name}():\n{steps}    return step_000\n"


def branchy(name: str, branches: int) -> str:
    body = "".join(
        f"    if code == {index}:\n        return {f'label_{index:02d}'!r}\n"
        for index in range(1, branches + 1)
    )
    return f"def {name}(code):\n{body}    return 'other'\n"


SEEDED += [
    case(
        "long-90",
        "seeded",
        "size",
        "architecture.function-lines",
        "a function of 90 lines (limit 80)",
        {
            "src/shop/long_report.py": long_function("long_report", 90),
            DEBT_TEST: test_debt(
                "from shop.long_report import long_report", "    assert long_report() == 0\n"
            ),
        },
    ),
    case(
        "long-120",
        "seeded",
        "size",
        "architecture.function-lines",
        "a function of 120 lines (limit 80)",
        {
            "src/shop/long_export.py": long_function("long_export", 120),
            DEBT_TEST: test_debt(
                "from shop.long_export import long_export", "    assert long_export() == 0\n"
            ),
        },
    ),
    case(
        "complex-17",
        "seeded",
        "size",
        "architecture.complexity",
        "a function of cyclomatic complexity 17 (limit 15)",
        {
            "src/shop/codes.py": branchy("classify_code", 16),
            DEBT_TEST: test_debt(
                "from shop.codes import classify_code",
                '    assert classify_code(1) == "label_01"\n',
            ),
        },
    ),
    # YAGNI
    case(
        "yagni-function",
        "seeded",
        "yagni",
        YAGNI,
        "a public function nothing names",
        {"src/shop/extras.py": "def unused_helper(value):\n    return value * 2\n"},
    ),
    case(
        "yagni-class",
        "seeded",
        "yagni",
        YAGNI,
        "a public class nothing names",
        {
            "src/shop/exporters.py": (
                "class UnusedExporter:\n    def export(self, rows):\n        return list(rows)\n"
            )
        },
    ),
    case(
        "yagni-async",
        "seeded",
        "yagni",
        YAGNI,
        "a public async function nothing names",
        {"src/shop/rates.py": "async def fetch_rates():\n    return {}\n"},
    ),
    case(
        "yagni-existing-module",
        "seeded",
        "yagni",
        YAGNI,
        "a public function nothing names, in the module the task touches",
        {},
        pricing_text=pricing(extra="\n\ndef legacy_total(value):\n    return value\n"),
    ),
    # Composition over inheritance
    case(
        "inherit-new-module",
        "seeded",
        "inheritance",
        INHERIT,
        "Frozen(Perishable), 4 levels, in a new module",
        {
            "src/shop/frozen.py": (
                "from shop.models import Perishable\n\n\n"
                "class Frozen(Perishable):\n    temperature = -18\n"
            ),
            DEBT_TEST: test_debt(
                "from shop.frozen import Frozen", "    assert Frozen().temperature == -18\n"
            ),
        },
    ),
    case(
        "inherit-same-module",
        "seeded",
        "inheritance",
        INHERIT,
        "Frozen(Perishable), 4 levels, in models.py",
        {
            "src/shop/models.py": MODELS_BASE
            + "\nclass Frozen(Perishable):\n    temperature = -18\n",
            DEBT_TEST: test_debt(
                "from shop.models import Frozen", "    assert Frozen().temperature == -18\n"
            ),
        },
    ),
    case(
        "inherit-attribute-base",
        "seeded",
        "inheritance",
        INHERIT,
        "DeepFrozen(models.Perishable), 4 levels, base named by attribute",
        {
            "src/shop/deep.py": (
                "from shop import models\n\n\n"
                "class DeepFrozen(models.Perishable):\n    temperature = -30\n"
            ),
            DEBT_TEST: test_debt(
                "from shop.deep import DeepFrozen", "    assert DeepFrozen().temperature == -30\n"
            ),
        },
    ),
    case(
        "inherit-new-chain",
        "seeded",
        "inheritance",
        INHERIT,
        "a new chain of 5 classes (4 levels)",
        {
            "src/shop/layers.py": (
                "class Layer0:\n    level = 0\n\n\n"
                + "".join(
                    f"class Layer{i}(Layer{i - 1}):\n    level = {i}\n\n\n" for i in range(1, 5)
                )
            ).rstrip("\n")
            + "\n",
            DEBT_TEST: test_debt(
                "from shop.layers import Layer4", "    assert Layer4.level == 4\n"
            ),
        },
    ),
]


def edit(text: str, old: str, new: str) -> str:
    if old not in text:
        raise ValueError(f"corpus error: {old!r} not found")
    return text.replace(old, new, 1)


ORDERS_TIDY = edit(ORDERS_BASE, "def validate_order(order):  \n", "def validate_order(order):\n")
MODELS_TIDY = MODELS_BASE.replace("\n\nclass ", "\n\n\nclass ")
REPORT_SPLIT = edit(
    REPORT_BASE,
    '    return "\\n".join(summary_line(name, total) for name, total in rows if total)\n',
    '    return "\\n".join(\n'
    "        summary_line(name, total) for name, total in rows if total\n"
    "    )\n",
)
REPORT_SPACED = edit(REPORT_BASE, "    width=len(title)+4\n", "    width = len(title) + 4\n")
README_WRAPPED = "# shop\n\nA small shop package with\nprices, orders and reports.\n"
PRICING_TRAILING = edit(
    PRICING_BASE,
    "def invoice_total(lines, tax_rate):   \n",
    "def invoice_total(lines, tax_rate):\n",
)
PRICING_BLANK = edit(PRICING_BASE, "\ndef unit_label", "\n\n\ndef unit_label")
PRICING_SPLIT = edit(PRICING_BASE, UNIT_LABEL, UNIT_LABEL_SPLIT)
PRICING_SPACED = edit(
    PRICING_BASE,
    "    taxes = round(subtotal*tax_rate, 2)\n",
    "    taxes = round(subtotal * tax_rate, 2)\n",
)
PRICING_ALL = edit(
    edit(
        edit(PRICING_TRAILING, "\ndef unit_label", "\n\n\ndef unit_label"),
        UNIT_LABEL,
        UNIT_LABEL_SPLIT,
    ),
    "    taxes = round(subtotal*tax_rate, 2)\n",
    "    taxes = round(subtotal * tax_rate, 2)\n",
)

REFORMATS = [
    case(
        "fmt-other-trailing",
        "clean",
        "reformat-other-file",
        REFORMAT,
        "trailing whitespace removed in orders.py (a file the task does not touch)",
        {"src/shop/orders.py": ORDERS_TIDY},
    ),
    case(
        "fmt-other-blank-lines",
        "clean",
        "reformat-other-file",
        REFORMAT,
        "two blank lines between the classes of models.py (a file the task does not touch)",
        {"src/shop/models.py": MODELS_TIDY},
    ),
    case(
        "fmt-other-split-call",
        "clean",
        "reformat-other-file",
        REFORMAT,
        "a call split over three lines in report.py (a file the task does not touch)",
        {"src/shop/report.py": REPORT_SPLIT},
    ),
    case(
        "fmt-other-operators",
        "clean",
        "reformat-other-file",
        REFORMAT,
        "spaces around operators in report.py (a file the task does not touch)",
        {"src/shop/report.py": REPORT_SPACED},
    ),
    case(
        "fmt-other-readme",
        "clean",
        "reformat-other-file",
        REFORMAT,
        "README.md re-wrapped (a file the task does not touch)",
        {"README.md": README_WRAPPED},
    ),
    case(
        "fmt-touched-trailing",
        "clean",
        "reformat-touched-file",
        None,
        "trailing whitespace removed in pricing.py, the file the task touches",
        {},
        pricing_text=pricing(base=PRICING_TRAILING),
    ),
    case(
        "fmt-touched-blank-lines",
        "clean",
        "reformat-touched-file",
        None,
        "two blank lines between the functions of pricing.py, the file the task touches",
        {},
        pricing_text=pricing(base=PRICING_BLANK),
    ),
    case(
        "fmt-touched-split-call",
        "clean",
        "reformat-touched-file",
        None,
        "a call split over three lines in pricing.py, the file the task touches",
        {},
        pricing_text=pricing(base=PRICING_SPLIT),
    ),
    case(
        "fmt-touched-operators",
        "clean",
        "reformat-touched-file",
        None,
        "spaces around an operator in pricing.py, the file the task touches",
        {},
        pricing_text=pricing(base=PRICING_SPACED),
    ),
    case(
        "fmt-touched-all",
        "clean",
        "reformat-touched-file",
        None,
        "the four tidy-ups together in pricing.py, the file the task touches",
        {},
        pricing_text=pricing(base=PRICING_ALL),
    ),
]
CASES = SEEDED + REFORMATS


def task_for(item: dict[str, Any], condition: str) -> dict[str, Any]:
    del condition
    return {
        "taskId": "task_h36_" + item["id"].replace("-", "_"),
        "title": f"Loyalty points ({item['id']})",
        "intent": "Add shop.pricing.loyalty_points(total): one point per 10 of the total.",
        "acceptanceCriteria": [
            {"criterionId": "ac_feature", "text": "loyalty_points(125) returns 12."}
        ],
    }


def detect(item: dict[str, Any], findings: list[dict[str, Any]]) -> bool:
    return bool(item["rule"]) and any(f["rule"] == item["rule"] for f in findings)


def principles_off(config: dict[str, Any]) -> dict[str, Any]:
    config.setdefault("verification", {}).setdefault("principles", {})["mode"] = "off"
    return config


CONDITIONS: dict[str, interception.ConfigBuilder | None] = {
    interception.DIRECT: None,
    "harness": interception.init_config,
    "harness-principles-off": principles_off,
}
SUITE = interception.Suite(
    suite_id="H36",
    title="principles DRY, KISS, YAGNI, boy scout and introduced debt",
    build_baseline=build_baseline,
    cases=CASES,
    conditions=CONDITIONS,
    task_for=task_for,
    detect=detect,
    rules=(*PRINCIPLE_RULES, *SIZE_RULES),
    relevant=("principles.", "architecture."),
)


# ----- introduced Ruff findings ------------------------------------------------------------------
def ruff_findings(root: Path, args: argparse.Namespace) -> list[str] | None:
    """``code path`` of every Ruff finding under ``root`` (``ruff check --isolated``), or
    ``None`` when Ruff is not in the project's environment."""
    try:
        proc = subprocess.run(
            [
                str(interception.venv_python(args.project_venv)),
                "-m",
                "ruff",
                "check",
                "--isolated",
                "--no-cache",
                "--output-format",
                "json",
                ".",
            ],
            cwd=root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        listed = json.loads(proc.stdout or "null")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if not isinstance(listed, list):
        return None
    found = []
    for item in listed:
        path = Path(str(item.get("filename") or ""))
        try:
            relative = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            relative = path.name
        found.append(f"{item.get('code')} {relative}")
    return sorted(found)


def ruff_version(args: argparse.Namespace) -> str | None:
    """The Ruff of the project's environment (``ruffIntroduced``), or ``None`` without it."""
    try:
        proc = subprocess.run(
            [str(interception.venv_python(args.project_venv)), "-m", "ruff", "--version"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() or None


def ruff_introduced(item: dict[str, Any], args: argparse.Namespace, rep: int) -> dict[str, Any]:
    run_dir = args.work / f"H36-{item['id']}-ruff-r{rep}-{stamp()}"
    before_dir, after_dir = run_dir / "before", run_dir / "after"
    for root in (before_dir, after_dir):
        root.mkdir(parents=True)
        build_baseline(root)
    interception.apply_change(after_dir, item)
    before = ruff_findings(before_dir, args)
    after = ruff_findings(after_dir, args)
    cleanup(run_dir, getattr(args, "keep_work", False))
    if before is None or after is None:
        return {"ruffIntroduced": None, "ruffIntroducedFindings": None}
    introduced = Counter(after) - Counter(before)
    return {
        "ruffIntroduced": sum(introduced.values()),
        "ruffIntroducedFindings": sorted(introduced.elements()),
    }


def runner(
    item: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    record = interception.run_one(SUITE, item, condition, args, rep)
    if "apparatusError" in record:
        raise RuntimeError(record["apparatusError"])
    if condition == interception.DIRECT:
        record.update(ruff_introduced(item, args, rep))
    rules = {f["rule"] for f in record.get("findings") or []}
    expected = {item["rule"]} if item["rule"] else set()
    record["unexpectedPrincipleFindings"] = sorted(
        r for r in rules if r.startswith("principles.") and r not in expected
    )
    record["brief"] = (
        f"detected={record.get('detected')} blocking={record.get('blockingFindings')} "
        f"delivered={record.get('delivered', '')}"
    )
    return record


# ----- summary ---------------------------------------------------------------------------------
def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    by_condition: dict[str, Any] = {}
    for condition, builder in CONDITIONS.items():
        rows = [r for r in ok if r["condition"] == condition]
        if not rows:
            continue
        if builder is None:
            by_condition[condition] = {
                "delivered": proportion(sum(r["delivered"] for r in rows), len(rows)),
                "notDelivered": sorted(r["case"] for r in rows if not r["delivered"]),
            }
            continue
        seeded = [r for r in rows if r["kind"] == "seeded"]
        reformats = [r for r in rows if r["kind"] == "clean"]
        touched = [r for r in reformats if r["group"] == "reformat-touched-file"]
        rules = sorted({str(r["expectedRule"]) for r in seeded})
        by_condition[condition] = {
            "detectionByRule": {
                rule: {
                    **proportion(
                        sum(r["detected"] for r in seeded if r["expectedRule"] == rule),
                        sum(1 for r in seeded if r["expectedRule"] == rule),
                    ),
                    "missed": sorted(
                        r["case"] for r in seeded if r["expectedRule"] == rule and not r["detected"]
                    ),
                }
                for rule in rules
            },
            "reformatOnlyOnOtherFile": proportion(
                sum(r["detected"] for r in reformats if r["group"] == "reformat-other-file"),
                sum(1 for r in reformats if r["group"] == "reformat-other-file"),
            ),
            "specificityOnReformats": {
                rule: proportion(
                    sum(
                        1
                        for r in (touched if rule == REFORMAT else reformats)
                        if not any(f["rule"] == rule for f in r["findings"])
                    ),
                    len(touched if rule == REFORMAT else reformats),
                )
                for rule in (*PRINCIPLE_RULES, *SIZE_RULES)
            },
            "reformatFalsePositive": proportion(
                sum(r["hasBlockingFinding"] for r in reformats), len(reformats)
            ),
            "reformatBlockedAnyCause": proportion(
                sum(r["blocked"] for r in reformats), len(reformats)
            ),
            "reformatUnexpectedPrinciple": {
                r["case"]: r["unexpectedPrincipleFindings"]
                for r in reformats
                if r["unexpectedPrincipleFindings"]
            },
            "seededBlocked": proportion(sum(r["blocked"] for r in seeded), len(seeded)),
            "lanes": sorted({str((r.get("lanes") or {}).get("classified")) for r in rows}),
            "rulesObserved": interception.rule_counts(rows),
        }
    direct = [r for r in ok if r["condition"] == interception.DIRECT]
    measured = [r for r in direct if isinstance(r.get("ruffIntroduced"), int)]
    groups = sorted({str(r["group"]) for r in direct})
    harness = (by_condition.get("harness") or {}).get("reformatFalsePositive") or {}
    summary: dict[str, Any] = {
        "suite": "H36",
        "title": "principles DRY, KISS, YAGNI, boy scout and introduced debt",
        "directEquivalent": "D* applies the change and runs the visible tests; it is a model "
        "of a direct agent, not an agent (CATALOGO.md §4.4)",
        "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION (a "
        "declared simulation); nobody decides at DECISION",
        "cases": {
            "seeded": sum(c["kind"] == "seeded" for c in CASES),
            "reformats": sum(c["kind"] == "clean" for c in CASES),
        },
        "byCondition": by_condition,
        "ruffIntroduced": {
            "measured": len(measured),
            "notEvaluated": sorted(r["case"] for r in direct if r not in measured),
            "byGroup": {
                group: sum(r["ruffIntroduced"] for r in measured if r["group"] == group)
                for group in groups
            },
            "byCase": {r["case"]: r["ruffIntroducedFindings"] for r in measured},
            "note": "the change is the same under every condition: a property of the case",
        },
    }
    summary["criteria"] = {
        "statement": "0 false positives (a blocking finding) on the 10 pure reformats under "
        "harness",
        "harnessReformatFalsePositive": harness,
        "met": harness.get("n") == 10 and harness.get("k") == 0,
        "evaluable": harness.get("n") == 10,
    }
    return summary


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H36",
            title="principles DRY, KISS, YAGNI, boy scout and introduced debt",
            stem="h36-principles",
            cases=CASES,
            conditions_of=lambda item: list(CONDITIONS),
            all_conditions=list(CONDITIONS),
            runner=runner,
            summarize=summarize,
            extra_environment=lambda args: {"ruffVersion": ruff_version(args)},
        )
    )
