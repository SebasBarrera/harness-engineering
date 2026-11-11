#!/usr/bin/env python3
"""H06: layer violations by class (``CATALOGO.md`` §2.1, ``PREREGISTRO.md``). No model.

A three-layer Python package written by the suite (``src/shop/domain``, ``src/shop/application``,
``src/shop/infrastructure``): ``domain`` depends on no other layer, ``application`` on ``domain``,
``infrastructure`` on ``domain`` and ``application``. The baseline already breaks one rule in two
files (``domain/legacy_report.py`` and ``domain/legacy_cache.py`` import ``infrastructure``).
Twenty changes, four per class: a new violation in an existing file, a pre-existing violation
left intact, a pre-existing violation touched, a new violating file, and a cycle between layers.
Every change adds a small function with its test.

Conditions: ``direct-equivalent`` (D*), ``harness`` (``harness init`` as written: the rules come
from the fixture agent's survey and the declared simulated person approves them with their
digest; ``layerCheck: differential``, ``baselineConflicts: allow``), ``harness-previous`` (the
same without ``layerCheck`` and ``baselineConflicts``: the behaviour before #93),
``harness-enforce-off`` (``architecture.enforce: off``) and, reported apart,
``harness-configured`` / ``harness-configured-previous`` (the same rules written in
``architecture.layers``/``allow``, differential and without the key). A case is detected when the
run has an ``architecture.layer-violation`` finding, at any severity.

Usage:
  python evaluation/deterministic/h06_layer_violations.py --out DIR [--harness PATH]
      [--work DIR] [--project-venv DIR] [--only id,...] [--conditions a,b] [--reps 1]
      [--summarize-only]

Output: DIR/environment.json, DIR/h06-layer-violations.jsonl (one record per case, condition
and repetition), DIR/h06-layer-violations-summary.json, DIR/configs/<condition>.yaml.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import interception  # noqa: E402
from common import Harness  # noqa: E402

RULE = "architecture.layer-violation"
CLASSES = (
    "new-violation",
    "preexisting-intact",
    "preexisting-touched",
    "new-file",
    "cycle",
)
SEEDED = ("new-violation", "new-file", "cycle")
LAYERS = [
    {"name": "domain", "paths": ["src/shop/domain/**"]},
    {"name": "application", "paths": ["src/shop/application/**"]},
    {"name": "infrastructure", "paths": ["src/shop/infrastructure/**"]},
]
ALLOW = {"application": ["domain"], "infrastructure": ["domain", "application"]}
CONFLICT_PAIR = "domain->infrastructure"

D = "src/shop/domain"
A = "src/shop/application"
I = "src/shop/infrastructure"  # noqa: E741

BASELINE: dict[str, str] = {
    "pyproject.toml": """\
[project]
name = "shop"
version = "0.1.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
""",
    ".gitignore": "__pycache__/\n*.pyc\n.venv/\n",
    "src/shop/__init__.py": '"""A small shop in three layers."""\n',
    f"{D}/__init__.py": '"""The domain: items and prices; it depends on no other layer."""\n',
    f"{D}/model.py": '''\
"""Items of an order."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    sku: str
    price_cents: int
    quantity: int


def line_total(item: Item) -> int:
    return item.price_cents * item.quantity
''',
    f"{D}/pricing.py": '''\
"""Order totals."""

from shop.domain.model import Item, line_total


def order_total(items: list[Item]) -> int:
    return sum(line_total(item) for item in items)
''',
    f"{D}/legacy_report.py": '''\
"""Legacy report: it reads the stored items directly (a dependency on infrastructure)."""

from shop.domain.pricing import order_total
from shop.infrastructure.storage import MemoryStorage


def stored_total(storage: MemoryStorage) -> int:
    return order_total(storage.all())
''',
    f"{D}/legacy_cache.py": '''\
"""Legacy cache counter (a dependency on infrastructure)."""

from shop.infrastructure import storage


def cached_count(store: storage.MemoryStorage) -> int:
    return len(store.all())
''',
    f"{A}/__init__.py": '"""The application: use cases over the domain."""\n',
    f"{A}/service.py": '''\
"""Checkout use case."""

from shop.domain.model import Item
from shop.domain.pricing import order_total


class CheckoutService:
    def __init__(self, storage):
        self._storage = storage

    def add(self, sku: str, price_cents: int, quantity: int) -> None:
        self._storage.save(Item(sku, price_cents, quantity))

    def total(self) -> int:
        return order_total(self._storage.all())
''',
    f"{I}/__init__.py": '"""Infrastructure: storage and wiring."""\n',
    f"{I}/storage.py": '''\
"""In-memory storage of items."""

from shop.domain.model import Item


class MemoryStorage:
    def __init__(self):
        self._items: dict[str, Item] = {}

    def save(self, item: Item) -> None:
        self._items[item.sku] = item

    def all(self) -> list[Item]:
        return [self._items[key] for key in sorted(self._items)]
''',
    f"{I}/wiring.py": '''\
"""Builds the use cases with their storage."""

from shop.application.service import CheckoutService
from shop.infrastructure.storage import MemoryStorage


def make_service() -> CheckoutService:
    return CheckoutService(MemoryStorage())
''',
    "tests/test_shop.py": """\
from shop.domain.legacy_cache import cached_count
from shop.domain.legacy_report import stored_total
from shop.domain.model import Item, line_total
from shop.domain.pricing import order_total
from shop.infrastructure.storage import MemoryStorage
from shop.infrastructure.wiring import make_service


def test_line_and_order_totals():
    items = [Item("A-1", 250, 2), Item("B-2", 100, 3)]
    assert line_total(items[0]) == 500
    assert order_total(items) == 800


def test_checkout_through_the_wiring():
    service = make_service()
    service.add("A-1", 250, 2)
    service.add("B-2", 100, 1)
    assert service.total() == 600


def test_legacy_report_and_cache():
    store = MemoryStorage()
    store.save(Item("A-1", 250, 2))
    assert stored_total(store) == 500
    assert cached_count(store) == 1
""",
}


def base(path: str) -> str:
    return BASELINE[path]


def append(path: str, text: str) -> str:
    return base(path).rstrip("\n") + "\n" + text


def add_import(path: str, line: str) -> str:
    """``path`` with ``line`` added after its last top-level import."""
    lines = base(path).splitlines(keepends=True)
    last = max(i for i, item in enumerate(lines) if item.startswith(("import ", "from ")))
    return "".join([*lines[: last + 1], line + "\n", *lines[last + 1 :]])


def test_file(name: str, body: str) -> dict[str, str]:
    return {f"tests/test_{name}.py": body}


def case(
    case_id: str,
    group: str,
    description: str,
    files: dict[str, str],
    *,
    pair: str | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "kind": "seeded" if group in SEEDED else "clean",
        "group": group,
        "rule": RULE if group in SEEDED else None,
        "pair": pair,
        "set": "shop",
        "description": description,
        "write": files,
        "delete": [],
        "results": {"architecture": SURVEY},
    }


SURVEY = {
    "style": "layered",
    "summary": "Three layers: domain, application, infrastructure.",
    "layers": LAYERS,
    "allow": ALLOW,
    "organization": "src/shop holds one package per layer.",
    "components": [
        {"name": "domain", "path": D, "responsibility": "Items and prices."},
        {"name": "application", "path": A, "responsibility": "Use cases."},
        {"name": "infrastructure", "path": I, "responsibility": "Storage and wiring."},
    ],
    "conventions": ["One package per layer under src/shop."],
    "guidance": ["domain imports no other layer; application imports domain only."],
}

CASES: list[dict[str, Any]] = [
    # ----- new violation in an existing file ---------------------------------------------------
    case(
        "nv-domain-to-application",
        "new-violation",
        "pricing (domain) gains a helper that builds the checkout service (application)",
        {
            f"{D}/pricing.py": append(
                f"{D}/pricing.py",
                "\n\ndef checkout_total(storage) -> int:\n"
                "    from shop.application.service import CheckoutService\n\n"
                "    return CheckoutService(storage).total()\n",
            ),
            **test_file(
                "checkout_total",
                "from shop.domain.pricing import checkout_total\n"
                "from shop.infrastructure.storage import MemoryStorage\n\n\n"
                "def test_checkout_total():\n    assert checkout_total(MemoryStorage()) == 0\n",
            ),
        },
        pair="domain->application",
    ),
    case(
        "nv-application-to-infrastructure",
        "new-violation",
        "the checkout service (application) gains a factory that uses the storage (infrastructure)",
        {
            f"{A}/service.py": add_import(
                f"{A}/service.py", "from shop.infrastructure.storage import MemoryStorage"
            )
            + "\n\ndef in_memory() -> CheckoutService:\n    return CheckoutService(MemoryStorage())\n",
            **test_file(
                "in_memory",
                "from shop.application.service import in_memory\n\n\n"
                "def test_in_memory():\n    assert in_memory().total() == 0\n",
            ),
        },
        pair="application->infrastructure",
    ),
    case(
        "nv-domain-to-infrastructure",
        "new-violation",
        "pricing (domain) gains a total of the stored items (infrastructure), a pair the baseline already breaks elsewhere",
        {
            f"{D}/pricing.py": append(
                f"{D}/pricing.py",
                "\n\ndef stored_order_total(store) -> int:\n"
                "    from shop.infrastructure.storage import MemoryStorage\n\n"
                "    assert isinstance(store, MemoryStorage)\n"
                "    return order_total(store.all())\n",
            ),
            **test_file(
                "stored_order_total",
                "from shop.domain.model import Item\n"
                "from shop.domain.pricing import stored_order_total\n"
                "from shop.infrastructure.storage import MemoryStorage\n\n\n"
                "def test_stored_order_total():\n"
                "    store = MemoryStorage()\n"
                '    store.save(Item("A-1", 100, 2))\n'
                "    assert stored_order_total(store) == 200\n",
            ),
        },
        pair=CONFLICT_PAIR,
    ),
    case(
        "nv-legacy-adds-application",
        "new-violation",
        "the legacy report (domain, already importing infrastructure) also imports the service (application)",
        {
            f"{D}/legacy_report.py": append(
                f"{D}/legacy_report.py",
                "\n\ndef service_total(storage: MemoryStorage) -> int:\n"
                "    from shop.application.service import CheckoutService\n\n"
                "    return CheckoutService(storage).total()\n",
            ),
            **test_file(
                "service_total",
                "from shop.domain.legacy_report import service_total\n"
                "from shop.infrastructure.storage import MemoryStorage\n\n\n"
                "def test_service_total():\n    assert service_total(MemoryStorage()) == 0\n",
            ),
        },
        pair="domain->application",
    ),
    # ----- pre-existing violation left intact --------------------------------------------------
    case(
        "pi-application-feature",
        "preexisting-intact",
        "the checkout service gains an item count (application, domain only)",
        {
            f"{A}/service.py": base(f"{A}/service.py").rstrip("\n")
            + "\n\n    def count(self) -> int:\n        return len(self._storage.all())\n",
            **test_file(
                "count",
                "from shop.infrastructure.wiring import make_service\n\n\n"
                "def test_count():\n"
                "    service = make_service()\n"
                '    service.add("A-1", 1, 1)\n'
                "    assert service.count() == 1\n",
            ),
        },
    ),
    case(
        "pi-domain-feature",
        "preexisting-intact",
        "the model gains a discount function (domain only)",
        {
            f"{D}/model.py": append(
                f"{D}/model.py",
                "\n\ndef discounted(item: Item, percent: int) -> int:\n"
                "    return line_total(item) * (100 - percent) // 100\n",
            ),
            **test_file(
                "discounted",
                "from shop.domain.model import Item, discounted\n\n\n"
                "def test_discounted():\n"
                '    assert discounted(Item("A-1", 100, 2), 10) == 180\n',
            ),
        },
    ),
    case(
        "pi-infrastructure-to-domain",
        "preexisting-intact",
        "the storage gains a total that uses pricing (infrastructure to domain, allowed)",
        {
            f"{I}/storage.py": add_import(
                f"{I}/storage.py", "from shop.domain.pricing import order_total"
            ).rstrip("\n")
            + "\n\n    def total(self) -> int:\n        return order_total(self.all())\n",
            **test_file(
                "storage_total",
                "from shop.domain.model import Item\n"
                "from shop.infrastructure.storage import MemoryStorage\n\n\n"
                "def test_storage_total():\n"
                "    store = MemoryStorage()\n"
                '    store.save(Item("A-1", 100, 3))\n'
                "    assert store.total() == 300\n",
            ),
        },
    ),
    case(
        "pi-test-imports-all",
        "preexisting-intact",
        "a new test imports every layer (tests are not judged)",
        test_file(
            "all_layers",
            "from shop.application.service import CheckoutService\n"
            "from shop.domain.model import Item\n"
            "from shop.infrastructure.storage import MemoryStorage\n\n\n"
            "def test_all_layers():\n"
            "    service = CheckoutService(MemoryStorage())\n"
            '    service.add("A-1", 10, 1)\n'
            '    assert service.total() == Item("A-1", 10, 1).price_cents\n',
        ),
    ),
    # ----- pre-existing violation touched --------------------------------------------------------
    case(
        "pt-report-new-function",
        "preexisting-touched",
        "the legacy report gains a function that uses the domain only",
        {
            f"{D}/legacy_report.py": append(
                f"{D}/legacy_report.py",
                "\n\ndef average_total(totals: list[int]) -> int:\n"
                "    return sum(totals) // len(totals) if totals else 0\n",
            ),
            **test_file(
                "average_total",
                "from shop.domain.legacy_report import average_total\n\n\n"
                "def test_average_total():\n    assert average_total([2, 4]) == 3\n",
            ),
        },
    ),
    case(
        "pt-report-docstring",
        "preexisting-touched",
        "the legacy report's function gains a docstring",
        {
            f"{D}/legacy_report.py": base(f"{D}/legacy_report.py").replace(
                "def stored_total(storage: MemoryStorage) -> int:\n",
                "def stored_total(storage: MemoryStorage) -> int:\n"
                '    """The total of every stored item, in cents."""\n',
            ),
            **test_file(
                "stored_total_doc",
                "from shop.domain.legacy_report import stored_total\n\n\n"
                "def test_stored_total_is_documented():\n"
                '    assert "cents" in stored_total.__doc__\n',
            ),
        },
    ),
    case(
        "pt-cache-imports-domain",
        "preexisting-touched",
        "the legacy cache gains an import of the domain model (allowed) and a function",
        {
            f"{D}/legacy_cache.py": add_import(
                f"{D}/legacy_cache.py", "from shop.domain.model import Item"
            )
            + "\n\ndef cached_skus(store: storage.MemoryStorage) -> list[str]:\n"
            "    items: list[Item] = store.all()\n"
            "    return [item.sku for item in items]\n",
            **test_file(
                "cached_skus",
                "from shop.domain.legacy_cache import cached_skus\n"
                "from shop.domain.model import Item\n"
                "from shop.infrastructure.storage import MemoryStorage\n\n\n"
                "def test_cached_skus():\n"
                "    store = MemoryStorage()\n"
                '    store.save(Item("A-1", 1, 1))\n'
                '    assert cached_skus(store) == ["A-1"]\n',
            ),
        },
    ),
    case(
        "pt-cache-new-function",
        "preexisting-touched",
        "the legacy cache gains a function without new imports",
        {
            f"{D}/legacy_cache.py": append(
                f"{D}/legacy_cache.py",
                "\n\ndef is_empty(store: storage.MemoryStorage) -> bool:\n"
                "    return cached_count(store) == 0\n",
            ),
            **test_file(
                "is_empty",
                "from shop.domain.legacy_cache import is_empty\n"
                "from shop.infrastructure.storage import MemoryStorage\n\n\n"
                "def test_is_empty():\n    assert is_empty(MemoryStorage())\n",
            ),
        },
    ),
    # ----- new violating file ---------------------------------------------------------------------
    case(
        "nf-domain-to-application",
        "new-file",
        "a new domain module imports the checkout service (application)",
        {
            f"{D}/promotions.py": '"""Promotions."""\n\n\n'
            "def promotion_total(storage) -> int:\n"
            "    from shop.application.service import CheckoutService\n\n"
            "    return CheckoutService(storage).total() * 9 // 10\n",
            **test_file(
                "promotions",
                "from shop.domain.promotions import promotion_total\n"
                "from shop.infrastructure.storage import MemoryStorage\n\n\n"
                "def test_promotion_total():\n    assert promotion_total(MemoryStorage()) == 0\n",
            ),
        },
        pair="domain->application",
    ),
    case(
        "nf-application-to-infrastructure",
        "new-file",
        "a new application module imports the storage (infrastructure)",
        {
            f"{A}/reports.py": '"""Reports."""\n\n'
            "from shop.infrastructure.storage import MemoryStorage\n\n\n"
            "def stored_skus(store: MemoryStorage) -> list[str]:\n"
            "    return [item.sku for item in store.all()]\n",
            **test_file(
                "reports",
                "from shop.application.reports import stored_skus\n"
                "from shop.infrastructure.storage import MemoryStorage\n\n\n"
                "def test_stored_skus():\n    assert stored_skus(MemoryStorage()) == []\n",
            ),
        },
        pair="application->infrastructure",
    ),
    case(
        "nf-domain-to-infrastructure",
        "new-file",
        "a new domain module imports the storage (infrastructure), a pair the baseline already breaks elsewhere",
        {
            f"{D}/audit.py": '"""Audit of the stored items."""\n\n'
            "from shop.infrastructure.storage import MemoryStorage\n\n\n"
            "def audit(store: MemoryStorage) -> int:\n"
            "    return sum(1 for item in store.all() if item.quantity <= 0)\n",
            **test_file(
                "audit",
                "from shop.domain.audit import audit\n"
                "from shop.infrastructure.storage import MemoryStorage\n\n\n"
                "def test_audit():\n    assert audit(MemoryStorage()) == 0\n",
            ),
        },
        pair=CONFLICT_PAIR,
    ),
    case(
        "nf-application-relative",
        "new-file",
        "a new application module imports the storage with a relative import",
        {
            f"{A}/exports.py": '"""Exports."""\n\n'
            "from ..infrastructure.storage import MemoryStorage\n\n\n"
            "def export_count(store: MemoryStorage) -> int:\n"
            "    return len(store.all())\n",
            **test_file(
                "exports",
                "from shop.application.exports import export_count\n"
                "from shop.infrastructure.storage import MemoryStorage\n\n\n"
                "def test_export_count():\n    assert export_count(MemoryStorage()) == 0\n",
            ),
        },
        pair="application->infrastructure",
    ),
    # ----- cycle between layers ---------------------------------------------------------------------
    case(
        "cy-domain-application-module",
        "cycle",
        "the model (domain) imports the application package at module level, which imports the domain",
        {
            f"{D}/model.py": add_import(f"{D}/model.py", "import shop.application")
            + "\n\ndef layer_name() -> str:\n    return shop.application.__name__\n",
            **test_file(
                "layer_name",
                "from shop.domain.model import layer_name\n\n\n"
                'def test_layer_name():\n    assert layer_name() == "shop.application"\n',
            ),
        },
        pair="domain->application",
    ),
    case(
        "cy-domain-application-function",
        "cycle",
        "the model (domain) imports the checkout service inside a function; the service imports the model",
        {
            f"{D}/model.py": append(
                f"{D}/model.py",
                "\n\ndef checkout_class_name() -> str:\n"
                "    from shop.application.service import CheckoutService\n\n"
                "    return CheckoutService.__name__\n",
            ),
            **test_file(
                "checkout_class_name",
                "from shop.domain.model import checkout_class_name\n\n\n"
                "def test_checkout_class_name():\n"
                '    assert checkout_class_name() == "CheckoutService"\n',
            ),
        },
        pair="domain->application",
    ),
    case(
        "cy-application-infrastructure-alias",
        "cycle",
        "the service (application) imports the wiring module (infrastructure, which imports the service) with import ... as",
        {
            f"{A}/service.py": base(f"{A}/service.py")
            + "\n\ndef default_service() -> CheckoutService:\n"
            "    import shop.infrastructure.wiring as wiring\n\n"
            "    return wiring.make_service()\n",
            **test_file(
                "default_service",
                "from shop.application.service import default_service\n\n\n"
                "def test_default_service():\n    assert default_service().total() == 0\n",
            ),
        },
        pair="application->infrastructure",
    ),
    case(
        "cy-application-infrastructure-from",
        "cycle",
        "the service (application) imports make_service from the wiring (infrastructure), which imports the service",
        {
            f"{A}/service.py": base(f"{A}/service.py") + "\n\ndef fresh_total() -> int:\n"
            "    from shop.infrastructure.wiring import make_service\n\n"
            "    return make_service().total()\n",
            **test_file(
                "fresh_total",
                "from shop.application.service import fresh_total\n\n\n"
                "def test_fresh_total():\n    assert fresh_total() == 0\n",
            ),
        },
        pair="application->infrastructure",
    ),
]


def build_baseline(root: Path) -> None:
    interception.write_files(root, BASELINE)


def task_for(item: dict[str, Any], condition: str) -> dict[str, Any]:
    return {
        "taskId": "task_h06_" + item["id"].replace("-", "_"),
        "title": f"Shop change ({item['id']})",
        "intent": f"Make this change to the shop package, with its test: {item['description']}.",
        "acceptanceCriteria": [
            {
                "criterionId": "ac_change",
                "text": f"The change works as described and its test passes: {item['description']}.",
            }
        ],
    }


def detect(item: dict[str, Any], findings: list[dict[str, Any]]) -> bool:
    return any(f["rule"] == RULE for f in findings)


def previous(config: dict[str, Any]) -> dict[str, Any]:
    """The behaviour before #93: no layerCheck, no baselineConflicts."""
    architecture = config.setdefault("architecture", {})
    architecture.pop("layerCheck", None)
    architecture.pop("baselineConflicts", None)
    return config


def enforce_off(config: dict[str, Any]) -> dict[str, Any]:
    config.setdefault("architecture", {})["enforce"] = "off"
    return config


def configured(differential: bool) -> interception.ConfigBuilder:
    def build(config: dict[str, Any]) -> dict[str, Any]:
        architecture = config.setdefault("architecture", {})
        architecture["layers"] = [dict(layer) for layer in LAYERS]
        architecture["allow"] = {key: list(value) for key, value in ALLOW.items()}
        if not differential:
            architecture.pop("layerCheck", None)
            architecture.pop("baselineConflicts", None)
        return config

    return build


def architecture_state(
    h: Harness, ws: Path, run_id: str, status: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    """What the survey proposed and what is in force (``.harness/architecture.json``)."""
    path = ws / ".harness" / "architecture.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = None
    record: dict[str, Any] = {"architectureState": None}
    if isinstance(state, dict):
        enforced = state.get("enforced") or {}
        record["architectureState"] = {
            "status": state.get("status"),
            "mode": state.get("mode"),
            "baselineConflicts": [
                {"from": c.get("from"), "to": c.get("to"), "imports": c.get("imports")}
                for c in state.get("baselineConflicts") or []
            ]
            if "baselineConflicts" in state
            else None,
            "enforcedAllow": enforced.get("allow") if enforced else None,
        }
    return record


SUITE = interception.Suite(
    suite_id="H06",
    title="layer violations by class",
    build_baseline=build_baseline,
    cases=CASES,
    conditions={
        interception.DIRECT: None,
        "harness": interception.init_config,
        "harness-previous": previous,
        "harness-enforce-off": enforce_off,
        "harness-configured": configured(True),
        "harness-configured-previous": configured(False),
    },
    task_for=task_for,
    detect=detect,
    rules=(RULE,),
    relevant=("architecture.",),
    after_decision=architecture_state,
    notes={
        "detected": "an architecture.layer-violation finding of any severity",
        "harness": "harness init as written; the rules come from the fixture survey and the simulated person approves them (layerCheck: differential, baselineConflicts: allow)",
        "harness-previous": "harness init without layerCheck and baselineConflicts (behaviour before #93)",
        "harness-enforce-off": "harness init with architecture.enforce: off (the catalogue's 'without architecture.enforce'; removing the key equals enforce)",
        "harness-configured*": "the same rules in architecture.layers/allow, differential and without the key (supplementary, reported apart)",
        "conflictPair": CONFLICT_PAIR,
        "pendingMitigation": "the repetition on itsdangerous with the simulated survey's rules is not done",
    },
)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = interception.summarize(SUITE, records)
    first = [r for r in records if r["rep"] == 1 and "apparatusError" not in r]
    classes: dict[str, Any] = {}
    for condition, builder in SUITE.conditions.items():
        rows = [r for r in first if r["condition"] == condition]
        if builder is None or not rows:
            continue
        seeded = [r for r in rows if r["kind"] == "seeded"]
        by_pair: dict[str, Any] = {}
        for pair in sorted({str(r.get("pair")) for r in seeded}):
            items = [r for r in seeded if str(r.get("pair")) == pair]
            by_pair[pair] = interception.proportion(sum(r["detected"] for r in items), len(items))
        conflicts = sorted(
            {json.dumps((r.get("architectureState") or {}).get("baselineConflicts")) for r in rows}
        )
        classes[condition] = {
            "detectedByClass": {
                name: interception.proportion(
                    sum(r["detected"] for r in rows if r["group"] == name),
                    sum(1 for r in rows if r["group"] == name),
                )
                for name in CLASSES
            },
            "blockedByClass": {
                name: interception.proportion(
                    sum(r["blocked"] for r in rows if r["group"] == name),
                    sum(1 for r in rows if r["group"] == name),
                )
                for name in CLASSES
            },
            "seededDetected": interception.proportion(
                sum(r["detected"] for r in seeded), len(seeded)
            ),
            "seededDetectedByPair": by_pair,
            "cleanWithFinding": sorted(
                r["case"] for r in rows if r["kind"] == "clean" and r["detected"]
            ),
            "seededMissed": sorted(r["case"] for r in seeded if not r["detected"]),
            "layerFindingSeverities": sorted(
                {f["severity"] for r in rows for f in r["findings"] if f["rule"] == RULE}
            ),
            "layerFindingPaths": sorted(
                {str(f["path"]) for r in rows for f in r["findings"] if f["rule"] == RULE}
            ),
            "baselineConflictsSeen": [json.loads(item) for item in conflicts],
            "architectureStatus": sorted(
                {str((r.get("architectureState") or {}).get("status")) for r in rows}
            ),
            "lanes": sorted({str((r.get("lanes") or {}).get("classified")) for r in rows}),
        }
    summary["layers"] = classes
    return summary


def layer_criterion(summary: dict[str, Any], differential: str, earlier: str) -> dict[str, Any]:
    layers = summary.get("layers") or {}
    current = layers.get(differential) or {}
    before = layers.get(earlier) or {}
    seeded = current.get("seededDetected") or {}
    intact = (current.get("detectedByClass") or {}).get("preexisting-intact") or {}
    before_clean = before.get("cleanWithFinding")
    return {
        "differential": differential,
        "previous": earlier,
        "seededDetected": seeded,
        "preexistingIntactWithFinding": intact,
        "previousCleanWithFinding": before_clean,
        "met": bool(
            seeded.get("n") == 12
            and (seeded.get("k") or 0) >= 0.95 * 12
            and intact.get("n") == 4
            and intact.get("k") == 0
            and before_clean
        ),
        "evaluable": seeded.get("n") == 12 and intact.get("n") == 4 and before_clean is not None,
    }


def criteria(summary: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered criterion (PREREGISTRO.md, H06), evaluated as it is on ``harness`` /
    ``harness-previous`` and, apart, on ``harness-configured`` / ``harness-configured-previous``."""
    return {
        "statement": "differential detects >= 95 % of the new violations (12 of 12 seeded) and 0 of the 4 pre-existing intact cases have the finding; the previous mode gives >= 1 false positive (reproduces #93)",
        **layer_criterion(summary, "harness", "harness-previous"),
        "configured": layer_criterion(summary, "harness-configured", "harness-configured-previous"),
    }


if __name__ == "__main__":
    raise SystemExit(
        interception.main(
            SUITE,
            __doc__ or "",
            "h06-layer-violations-summary.json",
            criteria,
            summarizer=summarize,
        )
    )
