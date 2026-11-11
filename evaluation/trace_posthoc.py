#!/usr/bin/env python3
"""H15: requirement -> test -> decision traceability, measured post hoc with one script for every
condition (``CATALOGO.md`` §2.2, ``PREREGISTRO.md``). No model.

The script reads a final workspace, the task and, for a governed run, the harness's state
database, and computes:

* for each identified requirement (the token that starts its text, ``A1. ``, ``[B12] ``, ``X8: ``,
  or its explicit ``requirementId``) and each criterion (its ``criterionId``): whether a test names
  it. A test names an id when a test file, ``Test*`` class or ``test*`` function has the id's
  tokens as a run of whole tokens of its name (any case: ``test_a1_rounding``, ``TestA1``), or when
  its code (decorators, docstring, comments, strings) holds the id as a whole word in the exact
  case (``pytest.param(..., id="A1")``, ``# covers A1``);
* for a governed run: whether an ``APPROVE`` or ``APPROVE_EXCEPTION`` decision is bound to the
  run's ChangeSet digest.

It is independent of the harness package (it never imports ``governed_harness``), so the measure
of the direct condition and of the harness is the same code. ``trace_completeness`` of
``harness_state.py`` (the 8 relations of 0.9.0 and those of 2.0.0) is taken from the run records
when they are given (``--records``).

Block A (post hoc, when its runs exist):
  python evaluation/trace_posthoc.py --runs DIR [--records FILE.jsonl ...] --out OUT.jsonl
reads every run directory ``<scenario>[-poor|-casual]-<condition>-<model>-r<rep>-<stamp>`` under
DIR (or ``DIR/<model>/``), its ``ws/`` workspace, the task of its scenario and its state database,
writes one record per run and ``OUT-summary.json`` with the fraction per condition and the
catalogue's criterion (harness >= direct in every scenario x model cell).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
TASKS = {
    ("greenfield", "full"): HERE / "tasks" / "greenfield-shipping.yaml",
    ("greenfield", "poor"): HERE / "tasks" / "greenfield-shipping-poor.yaml",
    ("security", "full"): HERE / "tasks" / "greenfield-alerts.yaml",
    ("security", "poor"): HERE / "tasks" / "greenfield-alerts-poor.yaml",
    ("brownfield", "full"): HERE / "tasks" / "brownfield-itsdangerous.yaml",
    ("brownfield", "poor"): HERE / "tasks" / "brownfield-itsdangerous-poor.yaml",
}
RUN_NAME = re.compile(
    r"^(?P<scenario>greenfield|brownfield|security)(?:-(?P<prompt>poor|casual))?"
    r"-(?P<condition>baseline|direct|harness-core|harness-anchored|harness-tiered|harness|clarify)"
    r"-(?P<model>.+)-r(?P<rep>\d+)-\d{8}T\d{6}Z$"
)
DIRECT_CONDITIONS = ("direct", "baseline")
_IDENTIFIER = r"[A-Z]{1,3}\d{1,3}(?:\.\d+)?"
LEADING = re.compile(rf"^\s*(?:\[(?P<b>{_IDENTIFIER})\][.:)]?|(?P<p>{_IDENTIFIER})[.:)])\s")
GENERATED = re.compile(r"^req_[0-9a-f]{32}$")
TEST_FILE = re.compile(r"^(?:test_.*|.*_test)\.py$")
SKIPPED_DIRECTORIES = {"node_modules", "__pycache__", "venv", "site-packages", "build", "dist"}
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEPARATORS = re.compile(r"[^A-Za-z0-9]+")
APPROVALS = ("APPROVE", "APPROVE_EXCEPTION")


# ----- identifiers -----------------------------------------------------------------------------
def identifiers(task: dict[str, Any]) -> list[dict[str, str]]:
    """The ids of the task: requirements (leading token, else explicit id) and criteria."""
    found: list[dict[str, str]] = []
    for item in task.get("requirements") or []:
        text = str(item.get("text") if isinstance(item, dict) else item)
        match = LEADING.match(text)
        explicit = str(item.get("requirementId") or "") if isinstance(item, dict) else ""
        if match:
            found.append({"kind": "requirement", "id": match.group("b") or match.group("p")})
        elif explicit and not GENERATED.fullmatch(explicit):
            found.append({"kind": "requirement", "id": explicit})
    for item in task.get("acceptanceCriteria") or []:
        if isinstance(item, dict) and item.get("criterionId"):
            found.append({"kind": "criterion", "id": str(item["criterionId"])})
    return found


def tokens(name: str) -> tuple[str, ...]:
    return tuple(part.lower() for part in _SEPARATORS.split(_CAMEL.sub("_", name)) if part)


def in_name(name: str, identifier: str) -> bool:
    wanted, have = tokens(identifier), tokens(name)
    width = len(wanted)
    return bool(wanted) and any(have[i : i + width] == wanted for i in range(len(have) - width + 1))


def in_text(text: str, identifier: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_]|\.\d)"
    return re.search(pattern, text) is not None


# ----- tests -----------------------------------------------------------------------------------
def test_files(workspace: Path) -> list[Path]:
    found: list[Path] = []
    for current, directories, files in os.walk(workspace):
        directories[:] = sorted(
            d for d in directories if not d.startswith(".") and d not in SKIPPED_DIRECTORIES
        )
        found += [Path(current) / f for f in sorted(files) if TEST_FILE.match(f)]
    return found


def nodes(workspace: Path) -> list[dict[str, Any]]:
    """Every test unit: the file (its name and module docstring), its ``Test*`` classes and its
    ``test*`` functions (name, and source lines with decorators and comments)."""
    found: list[dict[str, Any]] = []
    for path in test_files(workspace):
        relative = path.relative_to(workspace).as_posix()
        source = path.read_bytes().decode("utf-8", "replace")
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            found.append({"id": relative, "name": path.stem, "text": source})
            continue
        lines = source.splitlines()
        found.append({"id": relative, "name": path.stem, "text": ast.get_docstring(tree) or ""})
        found += list(scope(relative, tree.body, lines))
    return found


def scope(prefix: str, body: Iterable[ast.stmt], lines: list[str]) -> Iterable[dict[str, Any]]:
    for statement in body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            is_test = (
                statement.name.startswith("Test")
                if isinstance(statement, ast.ClassDef)
                else statement.name.startswith("test")
            )
            if not is_test:
                continue
            first = min([statement.lineno, *(d.lineno for d in statement.decorator_list)])
            text = "\n".join(lines[first - 1 : statement.end_lineno or statement.lineno])
            node_id = f"{prefix}::{statement.name}"
            if isinstance(statement, ast.ClassDef):
                header = "\n".join(lines[first - 1 : statement.lineno])
                docstring = ast.get_docstring(statement) or ""
                yield {"id": node_id, "name": statement.name, "text": header + "\n" + docstring}
                yield from scope(node_id, statement.body, lines)
            else:
                yield {"id": node_id, "name": statement.name, "text": text}


def analyse(workspace: Path, task: dict[str, Any]) -> dict[str, Any]:
    """Which ids of the task a test names, in ``workspace``."""
    units = nodes(workspace) if workspace.is_dir() else []
    items = []
    for item in identifiers(task):
        named = [
            u["id"]
            for u in units
            if in_name(u["name"], item["id"]) or in_text(u["text"], item["id"])
        ]
        items.append({**item, "named": bool(named), "tests": named[:10]})
    requirements = [i for i in items if i["kind"] == "requirement"]
    criteria = [i for i in items if i["kind"] == "criterion"]
    return {
        "testFiles": sorted({u["id"].split("::", 1)[0] for u in units}),
        "ids": items,
        "requirements": fraction(requirements),
        "criteria": fraction(criteria),
    }


def fraction(items: list[dict[str, Any]]) -> dict[str, Any]:
    named = sum(1 for i in items if i["named"])
    return {
        "named": named,
        "total": len(items),
        "rate": round(named / len(items), 3) if items else None,
    }


# ----- the decision ----------------------------------------------------------------------------
def decision_bound(database: Path | None, run_id: str | None = None) -> dict[str, Any]:
    """Whether an APPROVE or APPROVE_EXCEPTION is bound to the run's current ChangeSet digest
    (the latest ``execution`` record), read from the state database."""
    if database is None or not database.is_file():
        return {"database": False, "bound": False}
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:

        def records(kind: str) -> list[dict[str, Any]]:
            rows = connection.execute(
                "select payload_json from records where record_type = ? order by rowid", (kind,)
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

        executions = [
            e for e in records("execution") if not run_id or e.get("executionId") == run_id
        ]
        if not executions:
            return {"database": True, "bound": False, "reason": "no execution record"}
        execution = executions[-1]
        digest = execution.get("changeSetDigest")
        decisions = [
            d for d in records("decision") if d.get("executionId") == execution.get("executionId")
        ]
    finally:
        connection.close()
    bound = [
        d
        for d in decisions
        if d.get("decision") in APPROVALS and d.get("changeSetDigest") == digest
    ]
    return {
        "database": True,
        "bound": bool(bound),
        "decisions": sorted({str(d.get("decision")) for d in decisions}),
    }


# ----- block A -----------------------------------------------------------------------------------
def state_database(workspace: Path, run_dir: Path) -> Path | None:
    """``harness_state.state_db`` (the workspace's database or the run registry), repeated here so
    the script needs nothing but the standard library and PyYAML."""
    local = workspace / ".harness" / "state.db"
    if local.exists():
        return local
    found = sorted((run_dir / "state").glob("*/state.db")) if (run_dir / "state").is_dir() else []
    return found[0] if found else None


def run_dirs(root: Path) -> list[Path]:
    found = [p for p in sorted(root.iterdir()) if p.is_dir() and RUN_NAME.match(p.name)]
    for child in sorted(root.iterdir()):
        if child.is_dir() and not RUN_NAME.match(child.name):
            found += [p for p in sorted(child.iterdir()) if p.is_dir() and RUN_NAME.match(p.name)]
    return found


def task_of(run_dir: Path, scenario: str, prompt: str) -> dict[str, Any]:
    casual = run_dir / "task-casual.yaml"
    path = casual if prompt == "casual" and casual.is_file() else TASKS[(scenario, prompt)]
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def block_a(runs: Path, record_files: list[Path]) -> list[dict[str, Any]]:
    traces: dict[tuple[str, str, str, str, int], Any] = {}
    for path in record_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            condition = "direct" if item.get("condition") == "baseline" else item.get("condition")
            key = (
                str(item.get("scenario")),
                str(condition),
                str(item.get("prompt", "full")),
                str(item.get("model")),
                int(item.get("rep", 0)),
            )
            traces[key] = item.get("trace")
    out: list[dict[str, Any]] = []
    for run_dir in run_dirs(runs):
        match = RUN_NAME.match(run_dir.name)
        assert match is not None
        scenario, model = match.group("scenario"), match.group("model")
        prompt = match.group("prompt") or "full"
        condition = (
            "direct" if match.group("condition") in DIRECT_CONDITIONS else match.group("condition")
        )
        rep = int(match.group("rep"))
        workspace = run_dir / "ws"
        task = task_of(run_dir, scenario, prompt)
        record: dict[str, Any] = {
            "scenario": scenario,
            "prompt": prompt,
            "condition": condition,
            "model": model,
            "rep": rep,
            **analyse(workspace, task),
        }
        if condition not in DIRECT_CONDITIONS:
            record["decision"] = decision_bound(state_database(workspace, run_dir))
        record["traceCompleteness"] = traces.get((scenario, condition, prompt, model, rep))
        out.append(record)
    return out


def summarize_block_a(records: list[dict[str, Any]]) -> dict[str, Any]:
    cells: dict[str, dict[str, Any]] = {}
    for r in records:
        if r["prompt"] != "full":
            continue
        cell = cells.setdefault(f"{r['scenario']}/{r['model']}", {})
        bucket = cell.setdefault(r["condition"], {"named": 0, "total": 0, "runs": 0, "bound": 0})
        bucket["named"] += r["requirements"]["named"]
        bucket["total"] += r["requirements"]["total"]
        bucket["runs"] += 1
        bucket["bound"] += int(bool((r.get("decision") or {}).get("bound")))
    verdicts = {}
    for name, cell in cells.items():
        for bucket in cell.values():
            bucket["rate"] = (
                round(bucket["named"] / bucket["total"], 3) if bucket["total"] else None
            )
        direct, harness = cell.get("direct"), cell.get("harness")
        verdicts[name] = (
            None
            if not direct or not harness or direct["rate"] is None or harness["rate"] is None
            else harness["rate"] >= direct["rate"]
        )
    evaluable = bool(verdicts) and all(v is not None for v in verdicts.values())
    return {
        "suite": "H15 block A (post hoc)",
        "records": len(records),
        "cells": cells,
        "criteria": {
            "statement": "the fraction of requirements with a named test is reported per condition; the hypothesis holds if harness >= direct in every scenario x model cell",
            "byCell": verdicts,
            "met": evaluable and all(verdicts.values()),
            "evaluable": evaluable,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--runs", type=Path, required=True, help="directory of block A runs")
    parser.add_argument("--records", type=Path, nargs="*", default=[], help="block A JSONL records")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    records = block_a(args.runs, args.records)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8"
    )
    summary = summarize_block_a(records)
    summary_path = args.out.with_name(args.out.stem + "-summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(records)} records) and {summary_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
