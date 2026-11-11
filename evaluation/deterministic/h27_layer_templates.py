#!/usr/bin/env python3
"""H27: differential layer rules by architecture style (``CATALOGO.md`` §2.5,
``PREREGISTRO.md``). No model is called.

For each of the ten styles of the catalogue and two namings of its directories (*primary*, the
first pattern of each layer of the template under ``src/app``; *alternative*, another pattern of
the same layer under ``src/shop/sales``), a governed workspace gets the template's rules the way
a person gets them: on a new project a preparation run receives the fixture's recommendation (a
style other than the one chosen), the simulated person chooses the style with ``harness
architecture decide --style`` and the preparation run is cancelled. Then the baseline with one
pre-existing forbidden import (P) is committed and the measured run writes the change:

* ``violations``: N1 a new file importing the same forbidden layer as P, N2 an existing file
  gaining a forbidden import, N3 the file of P gaining a forbidden import of another layer, and
  N4 (reported apart) the file of P importing P's layer again on another line;
* ``clean-touch``: the file of P changed without a new import, and an allowed import added.

Conditions: ``harness-differential`` (``harness init``: ``layerCheck: differential``),
``harness-full`` (``layerCheck: full``, a control of the apparatus on the ``violations`` change
of the seven styles with a template) and ``direct-equivalent`` (D*: the change and the tests; it
is not an agent). The three styles without a template (modular-monolith, microservices,
event-driven) run both changes on the hexagonal baselines.

Usage:
  python evaluation/deterministic/h27_layer_templates.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only hexagonal-primary-violations]
      [--conditions harness-differential,harness-full,direct-equivalent] [--reps 1]
      [--summarize-only] [--keep-work]

Writes ``DIR/h27-layer-templates.jsonl``, ``DIR/h27-layer-templates-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import init_repo, simulated_person  # noqa: E402
from practice_support import cancel, commit, workspace, write_files  # noqa: E402
from resource_support import (  # noqa: E402
    cleanup,
    environment_python,
    finding_items,
    main,
    proportion,
    run_state,
    saved_config,
    stamp_dir,
)
from suite_support import bin_dir, portable_env, start_run  # noqa: E402

DIFFERENTIAL = "harness-differential"
FULL = "harness-full"
DIRECT = "direct-equivalent"
RULE = "architecture.layer-violation"
PERSON = "human.reviewer-simulated"

# Per style: B (the layer that may depend on nothing), T1 (a layer that may depend on B), T2
# (another layer), U (a layer T1 may not depend on), and the directory of every layer in the
# primary and the alternative naming (patterns of the template in resources/architecture).
TEMPLATES: dict[str, dict[str, Any]] = {
    "layered": {
        "roles": ("data", "services", "presentation", "presentation"),
        "primary": {"presentation": "presentation", "services": "services", "data": "data"},
        "alternative": {"presentation": "web", "services": "service", "data": "repositories"},
    },
    "hexagonal": {
        "roles": ("domain", "application", "adapters", "adapters"),
        "primary": {"domain": "domain", "application": "application", "adapters": "adapters"},
        "alternative": {"domain": "core", "application": "ports", "adapters": "infrastructure"},
    },
    "clean": {
        "roles": ("entities", "usecases", "adapters", "frameworks"),
        "primary": {
            "entities": "entities",
            "usecases": "usecases",
            "adapters": "adapters",
            "frameworks": "frameworks",
        },
        "alternative": {
            "entities": "entities",
            "usecases": "use_cases",
            "adapters": "interface_adapters",
            "frameworks": "drivers",
        },
    },
    "ddd": {
        "roles": ("domain", "application", "infrastructure", "interfaces"),
        "primary": {
            "domain": "domain",
            "application": "application",
            "infrastructure": "infrastructure",
            "interfaces": "interfaces",
        },
        "alternative": {
            "domain": "domain",
            "application": "application",
            "infrastructure": "infrastructure",
            "interfaces": "api",
        },
    },
    "mvvm": {
        "roles": ("model", "viewmodel", "view", "view"),
        "primary": {"model": "model", "viewmodel": "viewmodel", "view": "view"},
        "alternative": {"model": "models", "viewmodel": "viewmodels", "view": "views"},
    },
    "mvi": {
        "roles": ("model", "intent", "view", "view"),
        "primary": {"model": "model", "intent": "intent", "view": "view"},
        "alternative": {"model": "state", "intent": "intents", "view": "ui"},
    },
    "cqrs": {
        "roles": ("domain", "commands", "queries", "queries"),
        "primary": {"domain": "domain", "commands": "commands", "queries": "queries"},
        "alternative": {"domain": "domain", "commands": "write", "queries": "read"},
    },
}
NO_TEMPLATE = ("modular-monolith", "microservices", "event-driven")
PACKAGES = {"primary": ("src/app", "app"), "alternative": ("src/shop/sales", "shop.sales")}
NAMINGS = tuple(PACKAGES)
CHANGES = ("violations", "clean-touch")


# ----- the corpus ------------------------------------------------------------------------------
def layout(style: str, naming: str, module: str = "api") -> dict[str, Any]:
    """The roles, directories and package of a (style, naming); the styles without a template
    use the hexagonal layout."""
    template = TEMPLATES.get(style) or TEMPLATES["hexagonal"]
    root, package = PACKAGES[naming]
    bottom, middle, other, upper = template["roles"]
    return {
        "roles": {"B": bottom, "T1": middle, "T2": other, "U": upper},
        "dirs": template[naming],
        "root": root,
        "package": package,
        "mod": module,
    }


def _path(shape: dict[str, Any], layer: str, module: str) -> str:
    return f"{shape['root']}/{shape['dirs'][layer]}/{module}.py"


def _module(shape: dict[str, Any], layer: str, module: str) -> str:
    return f"{shape['package']}.{shape['dirs'][layer]}.{module}"


def baseline_files(style: str, naming: str, module: str = "api") -> dict[str, str]:
    """The code of the baseline: P in ``B/pre.py`` (B imports T1), ``T1/<module>.py`` imports B."""
    shape = layout(style, naming, module)
    roles = shape["roles"]
    b, t1 = roles["B"], roles["T1"]
    files: dict[str, str] = {}
    parts = shape["root"].split("/")
    for depth in range(2, len(parts) + 1):
        files["/".join(parts[:depth]) + "/__init__.py"] = ""
    for layer in shape["dirs"]:
        files[f"{shape['root']}/{shape['dirs'][layer]}/__init__.py"] = ""
        if layer not in (b, t1):
            files[_path(shape, layer, shape["mod"])] = (
                f"def {layer}_value() -> int:\n    return 10\n"
            )
    files[_path(shape, b, "entity")] = "def entity_value() -> int:\n    return 1\n"
    files[_path(shape, t1, shape["mod"])] = (
        f"from {_module(shape, b, 'entity')} import entity_value\n\n\n"
        f"def {t1}_value() -> int:\n    return entity_value() + 1\n"
    )
    files[_path(shape, b, "pre")] = (
        f"from {_module(shape, t1, shape['mod'])} import {t1}_value\n\n\n"
        f"def pre_value() -> int:\n    return {t1}_value() + 1\n"
    )
    files["tests/test_app.py"] = (
        f"from {_module(shape, b, 'pre')} import pre_value\n"
        f"from {_module(shape, t1, shape['mod'])} import {t1}_value\n\n\n"
        "def test_values() -> None:\n"
        f"    assert pre_value() == 3\n    assert {t1}_value() == 2\n"
    )
    return files


def change_files(style: str, naming: str, change: str, module: str = "api") -> dict[str, str]:
    shape = layout(style, naming, module)
    roles = shape["roles"]
    b, t1, t2, u = roles["B"], roles["T1"], roles["T2"], roles["U"]
    if change == "clean-touch":
        return {
            _path(shape, b, "extra"): "def extra_value() -> int:\n    return 5\n",
            _path(shape, b, "pre"): (
                f"from {_module(shape, t1, shape['mod'])} import {t1}_value\n\n\n"
                f"def pre_value() -> int:\n    return {t1}_value() + 1\n\n\n"
                "def pre_double() -> int:\n    return pre_value() * 2\n"
            ),
            _path(shape, t1, shape["mod"]): (
                f"from {_module(shape, b, 'entity')} import entity_value\n"
                f"from {_module(shape, b, 'extra')} import extra_value\n\n\n"
                f"def {t1}_value() -> int:\n    return entity_value() + 1\n\n\n"
                f"def {t1}_extra() -> int:\n    return extra_value()\n"
            ),
            "tests/test_app.py": (
                f"from {_module(shape, b, 'pre')} import pre_double, pre_value\n"
                f"from {_module(shape, t1, shape['mod'])} import {t1}_extra, {t1}_value\n\n\n"
                "def test_values() -> None:\n"
                f"    assert pre_value() == 3\n    assert {t1}_value() == 2\n\n\n"
                "def test_touch() -> None:\n"
                f"    assert pre_double() == 6\n    assert {t1}_extra() == 5\n"
            ),
        }
    return {
        # N1: a new file of B importing T1, the pair of P.
        _path(shape, b, "rule"): (
            f"from {_module(shape, t1, shape['mod'])} import {t1}_value\n\n\n"
            f"def rule_value() -> int:\n    return {t1}_value() * 2\n"
        ),
        # N2 (line 2): T1 imports U.
        _path(shape, t1, shape["mod"]): (
            f"from {_module(shape, b, 'entity')} import entity_value\n"
            f"from {_module(shape, u, shape['mod'])} import {u}_value\n\n\n"
            f"def {t1}_value() -> int:\n    return entity_value() + 1\n\n\n"
            f"def {t1}_other() -> int:\n    return {u}_value()\n"
        ),
        # P (line 1), N3 (line 2): B imports T2, N4 (line 3): B imports T1 again.
        _path(shape, b, "pre"): (
            f"from {_module(shape, t1, shape['mod'])} import {t1}_value\n"
            f"from {_module(shape, t2, shape['mod'])} import {t2}_value\n"
            f"from {_module(shape, t1, shape['mod'])} import {t1}_other\n\n\n"
            f"def pre_value() -> int:\n    return {t1}_value() + 1\n\n\n"
            f"def pre_more() -> int:\n    return {t2}_value() + {t1}_other()\n"
        ),
        "tests/test_app.py": (
            f"from {_module(shape, b, 'pre')} import pre_more, pre_value\n"
            f"from {_module(shape, b, 'rule')} import rule_value\n"
            f"from {_module(shape, t1, shape['mod'])} import {t1}_value\n\n\n"
            "def test_values() -> None:\n"
            f"    assert pre_value() == 3\n    assert {t1}_value() == 2\n\n\n"
            "def test_new() -> None:\n"
            "    assert rule_value() == 4\n    assert pre_more() == 20\n"
        ),
    }


def expected(
    style: str, naming: str, change: str, module: str = "api"
) -> dict[str, dict[str, Any]]:
    """The forbidden imports of the change (path, line, target layer): P is pre-existing."""
    shape = layout(style, naming, module)
    roles = shape["roles"]
    pre = _path(shape, roles["B"], "pre")
    items = {"P": {"path": pre, "line": 1, "layer": roles["T1"]}}
    if change == "violations":
        items.update(
            {
                "N1": {"path": _path(shape, roles["B"], "rule"), "line": 1, "layer": roles["T1"]},
                "N2": {
                    "path": _path(shape, roles["T1"], shape["mod"]),
                    "line": 2,
                    "layer": roles["U"],
                },
                "N3": {"path": pre, "line": 2, "layer": roles["T2"]},
                "N4": {"path": pre, "line": 3, "layer": roles["T1"]},
            }
        )
    return items


CASES = [
    {
        "id": f"{style}-{naming}-{change}",
        "style": style,
        "naming": naming,
        "change": change,
        "template": style in TEMPLATES,
        "module": "api",
        "set": "original",
    }
    for style in (*TEMPLATES, *NO_TEMPLATE)
    for naming in NAMINGS
    for change in CHANGES
] + [
    # Amendment 2 (PREREGISTRO.md): the same corpus with logic.py modules, reported apart.
    {
        "id": f"{style}-{naming}-{change}-logic",
        "style": style,
        "naming": naming,
        "change": change,
        "template": True,
        "module": "logic",
        "set": "module-logic",
    }
    for style in TEMPLATES
    for naming in NAMINGS
    for change in CHANGES
]


def conditions_of(case: dict[str, Any]) -> list[str]:
    if case["set"] == "original" and case["template"] and case["change"] == "violations":
        return [DIRECT, DIFFERENTIAL, FULL]
    return [DIRECT, DIFFERENTIAL]


# ----- D* --------------------------------------------------------------------------------------
def scaffold(root: Path) -> None:
    """A new project: configuration and documentation only."""
    write_files(
        root,
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "0.1.0"\n\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n',
            "README.md": "# App\n",
            ".gitignore": ".harness/\n__pycache__/\n.pytest_cache/\n",
        },
    )


def direct(case: dict[str, Any], args: argparse.Namespace, rep: int) -> dict[str, Any]:
    run_dir = stamp_dir(args, "h27", case["id"], DIRECT, f"r{rep}")
    ws = run_dir / "ws"
    ws.mkdir()
    scaffold(ws)
    write_files(ws, baseline_files(case["style"], case["naming"], case["module"]))
    init_repo(ws)
    write_files(ws, change_files(case["style"], case["naming"], case["change"], case["module"]))
    env = portable_env([bin_dir(args.project_venv)], run_dir / "state")
    try:
        proc = subprocess.run(
            [environment_python(args), "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=ws,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        code, tail = proc.returncode, (proc.stdout + proc.stderr)[-400:]
    except subprocess.TimeoutExpired:
        code, tail = -9, "TIMEOUT"
    cleanup(run_dir, args.keep_work)
    return {
        **{key: case[key] for key in ("style", "naming", "change", "template", "module", "set")},
        "testsExitCode": code,
        "delivered": code == 0,
        "testsTail": tail,
    }


# ----- governed --------------------------------------------------------------------------------
def choose_style(item: Any, style: str, task: dict[str, Any]) -> dict[str, Any]:
    """The preparation run: the recommendation, the simulated person's ``--style`` choice and
    the cancellation of the run."""
    h, ws, run_dir = item.h, item.ws, item.run_dir
    task_path = run_dir / "setup-task.yaml"
    task_path.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    h.run(ws, "task", "create", "--path", ".", "--file", str(task_path), label="setup task")
    code, started, _ = h.run(
        ws, "run", "start", "--path", ".", "--task", task["taskId"], label="setup run start"
    )
    run_id = started.get("executionId", "") if isinstance(started, dict) else ""
    waits: list[str] = []
    decided: int | None = None
    for _ in range(4):
        _, shown, _ = h.run(ws, "architecture", "show", "--path", ".", "--json", label="show")
        state = (shown.get("state") or {}) if isinstance(shown, dict) else {}
        if state.get("status") == "OPTIONS":
            decided, _, _ = h.run(
                ws,
                "architecture",
                "decide",
                "--path",
                ".",
                "--run",
                run_id,
                "--style",
                style,
                "--digest",
                str(state.get("digest")),
                "--actor",
                PERSON,
                "--rationale",
                f"simulated: the {style} template",
                "--no-continue",
                label="architecture decide",
            )
            break
        if code != 6:
            break
        before = len(waits)
        code = simulated_person(h, ws, run_id, task["taskId"], run_dir, waits, code)
        if len(waits) == before:
            break
    cancelled = cancel(item, run_id) if run_id else None
    return {
        "setupRunId": run_id,
        "setupWaits": waits,
        "styleDecideExitCode": decided,
        "setupCancelExitCode": cancelled,
    }


def governed(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    style, naming, change, module = case["style"], case["naming"], case["change"], case["module"]
    run_dir = stamp_dir(args, "h27", case["id"], condition, f"r{rep}")
    advise = "hexagonal" if style == "layered" else "layered"

    def mutate(config: dict[str, Any]) -> None:
        if condition == FULL:
            config.setdefault("architecture", {})["layerCheck"] = "full"

    item = workspace(run_dir, args, scaffold, {"adviseStyle": advise}, mutate=mutate)
    saved_config(args, condition, item)
    setup = choose_style(
        item,
        style,
        {
            "taskId": "task_h27_setup",
            "title": "Project skeleton",
            "intent": "Set up the project.",
            "acceptanceCriteria": [
                {"criterionId": "ac_tests", "text": "python -m pytest -q passes."}
            ],
        },
    )
    write_files(item.ws, baseline_files(style, naming, module))
    commit(item.ws, "baseline code")
    files = change_files(style, naming, change, module)
    item.write_spec({"adviseStyle": advise, "write": files}, "calls.jsonl")
    task = {
        "taskId": "task_h27_" + case["id"].replace("-", "_"),
        "title": "Extend the layers",
        "intent": "Add the new functions to the layers and test them.",
        "acceptanceCriteria": [{"criterionId": "ac_tests", "text": "python -m pytest -q passes."}],
        "metadata": {"ownedPaths": sorted(files)},
    }
    started = start_run(item.h, item.ws, run_dir, task)
    run_id = started["runId"]
    state = run_state(item, run_id)
    found = finding_items(item.h, item.ws, run_id)
    _, shown, _ = item.h.run(item.ws, "architecture", "show", "--path", ".", "--json")
    shown = shown if isinstance(shown, dict) else {}
    layer_findings = sorted(
        {
            (str(f["path"]), int(f["line"] or 0), str(f["message"]))
            for f in found
            if f["rule"] == RULE
        }
    )
    flagged = {
        name: any(
            path == item_["path"] and line == item_["line"] for path, line, _ in layer_findings
        )
        for name, item_ in expected(style, naming, change, module).items()
    }
    record = {
        "style": style,
        "naming": naming,
        "change": change,
        "template": case["template"],
        "module": module,
        "set": case["set"],
        **setup,
        "runId": run_id,
        "runExitCode": started["runExitCode"],
        "waits": started["waits"],
        **state,
        "rulesInForce": shown.get("rules"),
        "architectureStyle": (shown.get("state") or {}).get("style"),
        "architectureStatus": (shown.get("state") or {}).get("status"),
        "layerCheck": ((shown.get("configured") or {}).get("layerCheck")),
        "layerFindings": [
            {"path": path, "line": line, "message": message[:300]}
            for path, line, message in layer_findings
        ],
        "expected": expected(style, naming, change, module),
        "flagged": flagged,
        "findings": sorted({f"{f['severity']}:{f['rule']}" for f in found}),
        "deviations": item.deviations,
        "steps": item.h.steps,
        "brief": f"layer={len(layer_findings)} flagged={[k for k, v in flagged.items() if v]}",
    }
    cleanup(run_dir, args.keep_work)
    return record


def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    if condition == DIRECT:
        return direct(case, args, rep)
    return governed(case, condition, args, rep)


# ----- summary ---------------------------------------------------------------------------------
def unexpected(record: dict[str, Any]) -> list[str]:
    """Layer findings at no expected (path, line), or naming another layer than expected."""
    wanted = {(item["path"], item["line"]): item["layer"] for item in record["expected"].values()}
    extra = []
    for finding in record.get("layerFindings") or []:
        layer = wanted.get((finding["path"], finding["line"]))
        if layer is None or f"(layer {layer})" not in finding["message"]:
            extra.append(f"{finding['path']}:{finding['line']}: {finding['message'][:160]}")
    return extra


def style_entry(style: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    governed_rows = [r for r in rows if r["condition"] != DIRECT]
    diff = [r for r in governed_rows if r["condition"] == DIFFERENTIAL]
    full = [r for r in governed_rows if r["condition"] == FULL]
    entry: dict[str, Any] = {
        "template": style in TEMPLATES,
        "records": len(governed_rows),
        "rulesInForce": sorted(
            {
                ",".join(item["name"] for item in (r.get("rulesInForce") or {}).get("layers") or [])
                for r in governed_rows
            }
        ),
        "styleChosen": sorted({str(r.get("architectureStyle")) for r in governed_rows}),
        "directDelivered": proportion(
            sum(bool(r.get("delivered")) for r in rows if r["condition"] == DIRECT),
            sum(1 for r in rows if r["condition"] == DIRECT),
        ),
        "unexpectedLayerFindings": {
            f"{r['case']}/{r['condition']}": unexpected(r) for r in governed_rows if unexpected(r)
        },
    }
    if style not in TEMPLATES:
        entry["layerFindings"] = {r["case"]: len(r["layerFindings"]) for r in diff}
        return entry
    new = [
        (r["case"], name, bool(r["flagged"].get(name)))
        for r in diff
        if r["change"] == "violations"
        for name in ("N1", "N2", "N3")
    ]
    violations = [r for r in diff if r["change"] == "violations"]
    entry.update(
        {
            "newDetected": proportion(sum(hit for *_, hit in new), len(new)),
            "newMissed": sorted(f"{case}:{name}" for case, name, hit in new if not hit),
            "preexistingFlaggedDifferential": sorted(
                r["case"] for r in diff if r["flagged"].get("P")
            ),
            "n4FlaggedDifferential": proportion(
                sum(bool(r["flagged"].get("N4")) for r in violations), len(violations)
            ),
            "cleanTouchLayerFindings": {
                r["case"]: len(r["layerFindings"]) for r in diff if r["change"] == "clean-touch"
            },
            "fullControl": {r["case"]: {k: bool(v) for k, v in r["flagged"].items()} for r in full},
        }
    )
    return entry


def criteria_of(set_name: str, by_style: dict[str, Any], rows: list[dict[str, Any]]) -> dict:
    template_diff = [r for r in rows if r["condition"] == DIFFERENTIAL and r.get("template")]
    preexisting = [r["case"] for r in template_diff if r["flagged"].get("P")]
    per_style = {style: by_style[style]["newDetected"] for style in TEMPLATES if style in by_style}
    complete = len(per_style) == len(TEMPLATES) and all(v["n"] == 6 for v in per_style.values())
    expected_diff = len(TEMPLATES) * len(NAMINGS) * len(CHANGES)
    criteria: dict[str, Any] = {
        "newDetectionPerStyle": {
            "value": {s: f"{v['k']}/{v['n']}" for s, v in per_style.items()},
            "threshold": ">= 0.95 per style (6 of 6: N1-N3 of both namings)",
            "met": complete and all((v["rate"] or 0) >= 0.95 for v in per_style.values()),
        },
        "zeroPreexistingFalsePositives": {
            "value": f"{len(preexisting)} of {len(template_diff)} differential records flag P: "
            f"{preexisting}",
            "met": len(template_diff) == expected_diff and not preexisting,
        },
    }
    if set_name == "original":
        no_template = [
            r for r in rows if r["condition"] == DIFFERENTIAL and r.get("template") is False
        ]
        false_violations = [r["case"] for r in no_template if r["layerFindings"]]
        criteria["zeroFalseViolationsWithoutTemplate"] = {
            "value": f"{len(false_violations)} of {len(no_template)} records with a layer "
            f"finding: {false_violations}",
            "met": len(no_template) == len(NO_TEMPLATE) * len(NAMINGS) * len(CHANGES)
            and not false_violations,
        }
    return criteria


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    sets: dict[str, Any] = {}
    for set_name in ("original", "module-logic"):
        rows = [r for r in ok if r.get("set") == set_name]
        styles = (*TEMPLATES, *NO_TEMPLATE) if set_name == "original" else tuple(TEMPLATES)
        by_style = {
            style: style_entry(style, [r for r in rows if r.get("style") == style])
            for style in styles
        }
        sets[set_name] = {"byStyle": by_style, "criteria": criteria_of(set_name, by_style, rows)}
    return {
        "suite": "H27",
        "title": "differential layer rules by architecture style",
        "directEquivalent": "D* applies the change and runs the visible tests; it is not an agent",
        "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION and "
        "chooses the style with architecture decide --style (a declared simulation)",
        "cases": {name: sum(1 for c in CASES if c["set"] == name) for name in sets},
        "sets": sets,
        "criteria": {
            "ofRecord": sets["original"]["criteria"],
            "amendment2ModuleLogic": sets["module-logic"]["criteria"],
            "note": "Pre-registered in PREREGISTRO.md (H27, amendments 1 and 2): the criterion of "
            "record is the original corpus; module-logic is reported apart. Reported as is; a "
            "criterion with missing records (apparatus errors or a partial run) is not met.",
        },
    }


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H27",
            title="differential layer rules by architecture style",
            stem="h27-layer-templates",
            cases=CASES,
            conditions_of=conditions_of,
            all_conditions=[DIRECT, DIFFERENTIAL, FULL],
            runner=runner,
            summarize=summarize,
        )
    )
