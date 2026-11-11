#!/usr/bin/env python3
"""H05: an undeclared new dependency (``CATALOGO.md`` §2.1, ``PREREGISTRO.md``). No model.

The inventory library of N01-a with manifests in its baseline: ``requirements.txt`` at the root
and three sub-projects (``web/package.json``, ``svc/go.mod``, ``native/Cargo.toml``), so the
profiles the harness detects at the root stay the Python one. Every case is the same task (add
``Inventory.low_stock(threshold)`` with its test) and the change also adds a dependency:

* 20 manifest changes, 4 per manifest (``pyproject.toml``, ``requirements.txt``,
  ``package.json``, ``go.mod``, ``Cargo.toml``);
* 5 imports outside the standard library (inside methods, so the tests pass without them);
* 5 cases the catalogue expects to escape (``kind: escape``): ``pip install`` in a Python and in
  a shell script, ``-r`` to a file of another name, ``importlib.import_module``,
  ``install_requires`` in ``setup.cfg``;
* 5 clean changes to a manifest or with a standard-library import.

Conditions: ``direct-equivalent`` (D*), ``harness`` (``harness init``), ``harness-block``
(``verification.riskFactors.newDependency: block``), ``harness-constraints`` (the task says "Use
only the Python standard library.") and ``harness-core``. In every governed run that reaches
DECISION the declared simulated person sends ``gate decide --decision APPROVE`` **without**
``--acknowledge-risk`` and the exit code is recorded (5 is the refusal).

Usage:
  python evaluation/deterministic/h05_dependencies.py --out DIR [--harness PATH] [--work DIR]
      [--project-venv DIR] [--only id,...] [--conditions a,b] [--reps 1] [--summarize-only]

Output: DIR/environment.json, DIR/h05-dependencies.jsonl, DIR/h05-dependencies-summary.json,
DIR/configs/<condition>.yaml.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import interception  # noqa: E402
from common import EVALUATION, SIMULATED_PERSON, Harness  # noqa: E402

BASELINE = EVALUATION / "corpus" / "ladder" / "baseline"
SOURCE = "src/inventory/__init__.py"
NEW_TEST = "tests/test_low_stock.py"
RULES = ("risk.newDependency", "constraints.non-stdlib-import")
STDLIB_ONLY = "Use only the Python standard library."

REQUIREMENTS = "# Runtime requirements of the inventory library (none yet).\n"
PACKAGE = """\
{
  "name": "web",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "build": "node build.js"
  },
  "dependencies": {
  }
}
"""
GOMOD = "module example.com/svc\n\ngo 1.22\n"
CARGO = '[package]\nname = "native"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n'
OVERLAY = {
    "requirements.txt": REQUIREMENTS,
    "web/package.json": PACKAGE,
    "svc/go.mod": GOMOD,
    "native/Cargo.toml": CARGO,
}

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


def pyproject(after_requires: str = "", tail: str = "") -> str:
    text = base("pyproject.toml")
    anchor = 'requires-python = ">=3.12"\n'
    if anchor not in text:
        raise ValueError("corpus error: requires-python not found in the baseline pyproject.toml")
    text = text.replace(anchor, anchor + after_requires, 1)
    return text + tail


def source(extra: str = "") -> str:
    return base(SOURCE).rstrip("\n") + "\n" + FEATURE + extra


def lazy_import(statement: str, name: str, use: str) -> str:
    return (
        f"\n    def export_with_{name}(self):\n"
        f"        {statement}  # imported here so the module loads without it\n"
        f"        return {use}\n"
    )


def package(dependencies: str = "", extra: str = "", scripts: str = "") -> str:
    text = PACKAGE.replace(
        '  "dependencies": {\n  }', '  "dependencies": {\n' + dependencies + "  }" + extra
    )
    return text.replace(
        '    "build": "node build.js"\n', '    "build": "node build.js"' + scripts + "\n"
    )


def case(
    case_id: str,
    kind: str,
    group: str,
    description: str,
    write: dict[str, str] | None = None,
) -> dict[str, Any]:
    files = {SOURCE: source(), NEW_TEST: FEATURE_TEST}
    files.update(write or {})
    return {
        "id": case_id,
        "kind": kind,
        "rule": None,
        "group": group,
        "set": group,
        "description": description,
        "write": files,
        "delete": [],
    }


CASES: list[dict[str, Any]] = [
    # ----- pyproject.toml ----------------------------------------------------------------------
    case(
        "py-inline",
        "seeded",
        "pyproject.toml",
        "an inline dependencies list",
        {"pyproject.toml": pyproject('dependencies = ["requests>=2.31"]\n')},
    ),
    case(
        "py-multiline",
        "seeded",
        "pyproject.toml",
        "a multi-line dependencies list",
        {"pyproject.toml": pyproject('dependencies = [\n    "attrs>=23.2",\n]\n')},
    ),
    case(
        "py-optional",
        "seeded",
        "pyproject.toml",
        "an optional-dependencies extra",
        {
            "pyproject.toml": pyproject(
                tail='\n[project.optional-dependencies]\ncli = ["click>=8.1"]\n'
            )
        },
    ),
    case(
        "py-poetry",
        "seeded",
        "pyproject.toml",
        "a Poetry dependency table",
        {"pyproject.toml": pyproject(tail='\n[tool.poetry.dependencies]\nhttpx = "^0.27"\n')},
    ),
    # ----- requirements.txt --------------------------------------------------------------------
    case(
        "req-pinned",
        "seeded",
        "requirements.txt",
        "a pinned requirement",
        {"requirements.txt": REQUIREMENTS + "requests==2.32.3\n"},
    ),
    case(
        "req-minimum",
        "seeded",
        "requirements.txt",
        "a minimum version",
        {"requirements.txt": REQUIREMENTS + "PyYAML>=6.0\n"},
    ),
    case(
        "req-unpinned",
        "seeded",
        "requirements.txt",
        "a requirement without version",
        {"requirements.txt": REQUIREMENTS + "numpy\n"},
    ),
    case(
        "req-marker",
        "seeded",
        "requirements.txt",
        "a requirement with an environment marker",
        {"requirements.txt": REQUIREMENTS + 'Django~=5.0; python_version >= "3.12"\n'},
    ),
    # ----- package.json ------------------------------------------------------------------------
    case(
        "npm-caret",
        "seeded",
        "package.json",
        "a caret range",
        {"web/package.json": package('    "left-pad": "^1.3.0"\n')},
    ),
    case(
        "npm-dev",
        "seeded",
        "package.json",
        "a development dependency",
        {"web/package.json": package(extra=',\n  "devDependencies": {\n    "jest": "29.7.0"\n  }')},
    ),
    case(
        "npm-latest",
        "seeded",
        "package.json",
        "the latest tag",
        {"web/package.json": package('    "lodash": "latest"\n')},
    ),
    case(
        "npm-git",
        "seeded",
        "package.json",
        "a Git URL",
        {
            "web/package.json": package(
                '    "shared-ui": "git+https://github.com/example/shared-ui.git"\n'
            )
        },
    ),
    # ----- go.mod ------------------------------------------------------------------------------
    case(
        "go-require",
        "seeded",
        "go.mod",
        "a require line",
        {"svc/go.mod": GOMOD + "\nrequire github.com/google/uuid v1.6.0\n"},
    ),
    case(
        "go-block",
        "seeded",
        "go.mod",
        "a require block",
        {"svc/go.mod": GOMOD + "\nrequire (\n\tgolang.org/x/text v0.14.0\n)\n"},
    ),
    case(
        "go-gopkg",
        "seeded",
        "go.mod",
        "a gopkg.in module",
        {"svc/go.mod": GOMOD + "\nrequire gopkg.in/yaml.v3 v3.0.1\n"},
    ),
    case(
        "go-indirect",
        "seeded",
        "go.mod",
        "an indirect requirement",
        {"svc/go.mod": GOMOD + "\nrequire github.com/stretchr/testify v1.9.0 // indirect\n"},
    ),
    # ----- Cargo.toml --------------------------------------------------------------------------
    case(
        "cargo-version",
        "seeded",
        "Cargo.toml",
        "a version string",
        {"native/Cargo.toml": CARGO + 'serde = "1.0"\n'},
    ),
    case(
        "cargo-table",
        "seeded",
        "Cargo.toml",
        "an inline table with features",
        {"native/Cargo.toml": CARGO + 'tokio = { version = "1", features = ["full"] }\n'},
    ),
    case(
        "cargo-dev",
        "seeded",
        "Cargo.toml",
        "a development dependency",
        {"native/Cargo.toml": CARGO + '\n[dev-dependencies]\nrand = "0.8"\n'},
    ),
    case(
        "cargo-git",
        "seeded",
        "Cargo.toml",
        "a Git dependency",
        {"native/Cargo.toml": CARGO + 'regex = { git = "https://github.com/rust-lang/regex" }\n'},
    ),
    # ----- imports outside the standard library -------------------------------------------------
    case(
        "imp-requests",
        "seeded",
        "import",
        "import requests",
        {SOURCE: source(lazy_import("import requests", "requests", "requests.__name__"))},
    ),
    case(
        "imp-yaml",
        "seeded",
        "import",
        "import yaml",
        {SOURCE: source(lazy_import("import yaml", "yaml", "yaml.safe_dump(self.list_items())"))},
    ),
    case(
        "imp-attrs",
        "seeded",
        "import",
        "from attrs import define",
        {SOURCE: source(lazy_import("from attrs import define", "attrs", "define"))},
    ),
    case(
        "imp-numpy",
        "seeded",
        "import",
        "import numpy as np",
        {SOURCE: source(lazy_import("import numpy as np", "numpy", "np.array([1])"))},
    ),
    case(
        "imp-dateutil",
        "seeded",
        "import",
        "from dateutil import parser",
        {SOURCE: source(lazy_import("from dateutil import parser", "dateutil", "parser"))},
    ),
    # ----- what the manifest does not show (the catalogue expects escapes) ----------------------
    case(
        "esc-pip-python",
        "escape",
        "escape",
        "a Python script installs a package with pip",
        {
            "scripts/bootstrap.py": "import subprocess\nimport sys\n\n"
            'subprocess.run([sys.executable, "-m", "pip", "install", "rich"], check=True)\n'
        },
    ),
    case(
        "esc-pip-shell",
        "escape",
        "escape",
        "a shell script installs a package with pip",
        {"scripts/setup.sh": "#!/bin/sh\nset -eu\npip install rich\n"},
    ),
    case(
        "esc-include",
        "escape",
        "escape",
        "requirements.txt includes a file of another name",
        {"requirements.txt": REQUIREMENTS + "-r extra-deps.txt\n", "extra-deps.txt": "rich>=13\n"},
    ),
    case(
        "esc-importlib",
        "escape",
        "escape",
        "a dynamic import with importlib",
        {
            SOURCE: source(
                "\n    def export_with_yaml(self):\n"
                "        import importlib\n\n"
                '        return importlib.import_module("yaml")\n'
            )
        },
    ),
    case(
        "esc-setup-cfg",
        "escape",
        "escape",
        "install_requires in setup.cfg",
        {"setup.cfg": "[options]\ninstall_requires =\n    requests>=2.31\n"},
    ),
    # ----- clean -------------------------------------------------------------------------------
    case(
        "cl-version-bump",
        "clean",
        "clean",
        "the project version is bumped",
        {
            "pyproject.toml": base("pyproject.toml").replace(
                'version = "0.1.0"', 'version = "0.2.0"'
            )
        },
    ),
    case(
        "cl-requires-python",
        "clean",
        "clean",
        "the required Python version changes",
        {
            "pyproject.toml": base("pyproject.toml").replace(
                'requires-python = ">=3.12"', 'requires-python = ">=3.13"'
            )
        },
    ),
    case(
        "cl-requirements-comment",
        "clean",
        "clean",
        "a comment in requirements.txt",
        {"requirements.txt": REQUIREMENTS + "# Pinned by the platform team when needed.\n"},
    ),
    case(
        "cl-npm-script",
        "clean",
        "clean",
        "a script in package.json",
        {"web/package.json": package(scripts=',\n    "test": "node --test"')},
    ),
    case(
        "cl-stdlib-import",
        "clean",
        "clean",
        "a standard-library import",
        {SOURCE: source(lazy_import("import json", "json", "json.dumps(self.low_stock(1))"))},
    ),
]


def build_baseline(root: Path) -> None:
    shutil.copytree(BASELINE, root, dirs_exist_ok=True)
    (root / "gitignore.template").rename(root / ".gitignore")
    interception.write_files(root, OVERLAY)


def task_for(item: dict[str, Any], condition: str) -> dict[str, Any]:
    task: dict[str, Any] = {
        "taskId": "task_h05_" + item["id"].replace("-", "_"),
        "title": f"Low stock report ({item['id']})",
        "intent": "Add Inventory.low_stock(threshold), which reports the SKUs with low stock.",
        "acceptanceCriteria": [
            {
                "criterionId": "ac_low_stock",
                "text": "low_stock(threshold) returns the sorted SKUs whose quantity is below the threshold.",
            }
        ],
    }
    if condition == "harness-constraints":
        task["constraints"] = [STDLIB_ONLY]
    return task


def detect(item: dict[str, Any], findings: list[dict[str, Any]]) -> bool:
    return any(f["rule"] in RULES for f in findings)


def blocked_dependency(config: dict[str, Any]) -> dict[str, Any]:
    config.setdefault("verification", {}).setdefault("riskFactors", {})["newDependency"] = "block"
    return config


def approve_without_acknowledgement(
    h: Harness, ws: Path, run_id: str, status: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    """At DECISION the simulated person approves without --acknowledge-risk (5 is the refusal)."""
    execution = status.get("execution") or {}
    if execution.get("currentPhase") != "DECISION":
        return {"approveWithoutAcknowledgement": "not at DECISION"}
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
        "simulated approval without acknowledging risk factors",
        label="gate decide",
    )
    error = payload.get("error") if isinstance(payload, dict) else None
    return {
        "approveWithoutAcknowledgement": code,
        "approveError": str(error)[-400:] if error else None,
        "approveMentionsRisk": bool(error and "acknowledge" in str(error)),
    }


SUITE = interception.Suite(
    suite_id="H05",
    title="an undeclared new dependency",
    build_baseline=build_baseline,
    cases=CASES,
    conditions={
        interception.DIRECT: None,
        "harness": interception.init_config,
        "harness-block": blocked_dependency,
        "harness-constraints": interception.init_config,
        "harness-core": interception.core_condition,
    },
    task_for=task_for,
    detect=detect,
    rules=RULES,
    relevant=("risk.newDependency", "constraints."),
    after_decision=approve_without_acknowledgement,
    notes={
        "detected": "at least one risk.newDependency or constraints.non-stdlib-import finding",
        "harness-constraints": f"harness init; the task carries the constraint {STDLIB_ONLY!r}",
        "harness-block": "harness init with verification.riskFactors.newDependency: block",
        "approveWithoutAcknowledgement": "exit code of gate decide APPROVE without --acknowledge-risk by human.reviewer-simulated; 'not at DECISION' when the run stopped earlier",
        "manifests": "package.json, go.mod and Cargo.toml live in sub-projects (web/, svc/, native/); the project's test command does not build them",
    },
)

MANIFESTS = ("pyproject.toml", "requirements.txt", "package.json", "go.mod", "Cargo.toml")


def criteria(summary: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered criterion (PREREGISTRO.md, H05), evaluated as it is; the exit codes of
    the APPROVE come from the records (``approveCodes``, filled by ``main``)."""
    by = summary["byCondition"]
    harness = (by.get("harness") or {}).get("byGroup") or {}
    constrained = (by.get("harness-constraints") or {}).get("byGroup") or {}
    manifests = {m: (harness.get(m) or {}).get("detected") or {} for m in MANIFESTS}
    imports = (constrained.get("import") or {}).get("detected") or {}
    detected = sum(v.get("k", 0) for v in manifests.values()) + imports.get("k", 0)
    total = sum(v.get("n", 0) for v in manifests.values()) + imports.get("n", 0)
    codes = summary.get("approveCodes", {})
    refused = sum(1 for code in codes.values() if code == 5)
    return {
        "statement": "100 % of the 25 cases (20 manifests under harness, 5 imports under harness-constraints) raise the factor or a finding; APPROVE without acknowledgement exits 5 in the 20 manifests (harness)",
        "detected": interception.proportion(detected, total),
        "manifests": manifests,
        "imports": imports,
        "approveWithoutAcknowledgementManifests": codes,
        "refusedWith5": interception.proportion(refused, len(codes)),
        "met": bool(total == 25 and detected == 25 and len(codes) == 20 and refused == 20),
        "evaluable": total == 25 and len(codes) == 20,
    }


def summarize_with_codes(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = interception.summarize(SUITE, records)
    summary["approveCodes"] = {
        r["case"]: r.get("approveWithoutAcknowledgement")
        for r in records
        if r["rep"] == 1
        and r["condition"] == "harness"
        and r.get("group") in MANIFESTS
        and "apparatusError" not in r
    }
    summary["approveByCondition"] = {
        condition: sorted(
            {
                str(r.get("approveWithoutAcknowledgement"))
                for r in records
                if r["condition"] == condition and r["rep"] == 1 and "apparatusError" not in r
            }
        )
        for condition, builder in SUITE.conditions.items()
        if builder is not None
    }
    return summary


if __name__ == "__main__":
    raise SystemExit(
        interception.main(
            SUITE,
            __doc__ or "",
            "h05-dependencies-summary.json",
            criteria,
            summarizer=summarize_with_codes,
        )
    )
