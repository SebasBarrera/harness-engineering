#!/usr/bin/env python3
"""N01-a: the verification ladder on a corpus of seeded changes with known truth. No model.

Every case of ``evaluation/corpus/ladder/cases.yaml`` is one task on the inventory library
(parts A and B in ``baseline/``) whose change is applied in patch mode by the ``simulated``
provider, so the change is exactly the seeded one. The project runs with the configuration
``harness init`` writes (certification under ``verification.ladder``, light mutation under
``verification.mutation``; ``--mutation-mode`` overrides the mode). Waits before DECISION are
answered by the declared simulated person (``common.simulated_person``); no decision is taken.

For each case the record holds the certification (run and per criterion), the mutation findings
(``tests.change-not-exercised``, ``tests.weak``, ``tests.broken``), the test-quality findings,
the gate and where the run stopped, and the oracle: the hidden tests of the case's part run on a
separate copy of the baseline with the change applied (``defectiveObserved``), compared with the
declared truth. The summary computes the sensitivity and specificity of each signal against two
truths: the change is defective, and the evidence does not reach the declared rung.

Usage:
  python evaluation/deterministic/ladder_corpus.py --out DIR [--harness PATH] [--work DIR]
      [--project-venv DIR] [--only id,...] [--mutation-mode warn|enforce] [--reps 1]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    EVALUATION,
    Harness,
    add_common_arguments,
    environment,
    init_repo,
    isolated_env,
    simulated_person,
    stamp,
    write_json,
    write_jsonl,
)

CORPUS = EVALUATION / "corpus" / "ladder"
HIDDEN = [
    EVALUATION / "longitudinal" / "hidden" / "test_hidden_inventory.py",
    CORPUS / "hidden" / "test_hidden_cli.py",
]
MUTATION_RULES = {"tests.change-not-exercised", "tests.weak", "tests.broken"}
FLAGGED_CERTIFICATION = {"NOT_CERTIFIED", "PARTIAL"}


def load_corpus() -> tuple[dict[str, Any], dict[str, Any]]:
    return yaml.safe_load((CORPUS / "cases.yaml").read_text()), yaml.safe_load(
        (CORPUS / "snippets.yaml").read_text()
    )


def copy_baseline(target: Path) -> None:
    shutil.copytree(CORPUS / "baseline", target)
    (target / "gitignore.template").rename(target / ".gitignore")


def change_files(
    case: dict[str, Any], corpus: dict[str, Any], snippets: dict[str, Any]
) -> dict[str, str]:
    """The files the seeded change writes (path -> whole new content)."""
    files: dict[str, str] = {}
    part = corpus["parts"][case["part"]]
    if case.get("source"):
        block = snippets["source"][case["source"]]
        base = (CORPUS / "baseline" / part["source_file"]).read_text()
        lines = base.splitlines()
        # The standard-library import block is rebuilt in isort order (plain imports, then
        # from-imports, each sorted) with the block's imports, so Ruff's I001 stays quiet.
        positions = [i for i, line in enumerate(lines) if line.startswith(("import ", "from "))]
        first, last = positions[0], positions[-1]
        existing = [line for line in lines[first : last + 1] if line.strip()]
        merged = set(existing) | set(block["imports"])
        ordered = sorted(item for item in merged if item.startswith("import ")) + sorted(
            item for item in merged if item.startswith("from ")
        )
        new_lines = lines[:first] + ordered + lines[last + 1 :]
        method = "\n".join(
            ("    " + line if line.strip() else "") for line in block["code"].rstrip().splitlines()
        )
        files[part["source_file"]] = "\n".join(new_lines).rstrip() + "\n\n" + method + "\n"
    for path, name in (case.get("files") or {}).items():
        files[path] = snippets["files"][name]
    for path, name in (case.get("tests") or {}).items():
        files[path] = snippets["tests"][name]
    return files


def task_for(case: dict[str, Any], corpus: dict[str, Any], files: dict[str, str]) -> dict[str, Any]:
    part = corpus["parts"][case["part"]]
    criteria = []
    probes = []
    for criterion_id, text in part["criteria"].items():
        level = case["levels"][criterion_id]
        verification = {"level": level} if isinstance(level, str) else dict(level)
        criteria.append({"criterionId": criterion_id, "text": text, "verification": verification})
        if "probe" in verification:
            probe = {"id": verification["probe"], **corpus["probes"][verification["probe"]]}
            probes.append(probe)
    patches = []
    for path, content in files.items():
        exists = (CORPUS / "baseline" / path).exists()
        patches.append(
            {"path": path, "operation": "replace" if exists else "create", "content": content}
        )
    task: dict[str, Any] = {
        "taskId": "task_" + case["id"].replace("-", "_"),
        "title": f"{part['title']} ({case['id']})",
        "intent": part["intent"],
        "acceptanceCriteria": criteria,
        "constraints": ["Use only the Python standard library."],
        "implementation": {"mode": "patch", "patches": patches},
    }
    if probes:
        task["probes"] = probes
    return task


def oracle(
    case: dict[str, Any],
    corpus: dict[str, Any],
    files: dict[str, str],
    directory: Path,
    python: str,
) -> dict[str, Any]:
    """The hidden tests of the case's part on a copy of the baseline with the change applied."""
    shutil.rmtree(directory, ignore_errors=True)
    copy_baseline(directory)
    for path, content in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    hidden = directory / "_hidden_eval"
    hidden.mkdir()
    for item in HIDDEN:
        shutil.copy2(item, hidden / item.name)
    selector = " or ".join(corpus["parts"][case["part"]]["oracle"])
    junit = directory / "hidden-junit.xml"
    proc = subprocess.run(
        [
            python,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(directory),
            "-k",
            selector,
            f"--junitxml={junit}",
            str(hidden),
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        env={
            "PATH": str(Path(python).parent) + ":/usr/bin:/bin",
            "PYTHONPATH": str(directory / "src"),
            "HOME": str(directory),
        },
    )
    import xml.etree.ElementTree as ET

    passed = failed = 0
    failures: list[str] = []
    if junit.exists():
        for test in ET.parse(junit).getroot().iter("testcase"):
            if test.find("failure") is not None or test.find("error") is not None:
                failed += 1
                failures.append(test.get("name", ""))
            elif test.find("skipped") is None:
                passed += 1
    return {"passed": passed, "failed": failed, "failures": failures, "exitCode": proc.returncode}


def run_case(
    case: dict[str, Any],
    corpus: dict[str, Any],
    snippets: dict[str, Any],
    args: argparse.Namespace,
    rep: int,
) -> dict[str, Any]:
    run_dir = args.work / f"ladder-{case['id']}-r{rep}-{stamp()}"
    ws = run_dir / "ws"
    copy_baseline(ws)
    init_repo(ws)
    h = Harness(args.harness, isolated_env([args.project_venv / "bin"], run_dir / "harness-state"))
    h.run(ws, "init", "--path", ".", label="init")
    config_path = ws / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text())
    if args.mutation_mode:
        config["verification"]["mutation"]["mode"] = args.mutation_mode
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    files = change_files(case, corpus, snippets)
    task = task_for(case, corpus, files)
    task_path = run_dir / "task.yaml"
    task_path.write_text(yaml.safe_dump(task, sort_keys=False, allow_unicode=True))
    code, created, _ = h.run(
        ws, "task", "create", "--path", ".", "--file", str(task_path), label="task create"
    )
    record: dict[str, Any] = {
        "case": case["id"],
        "rep": rep,
        "part": case["part"],
        "truth": case["truth"],
        "levels": case["levels"],
        "taskCreateExitCode": code,
    }
    code, started, _ = h.run(
        ws, "run", "start", "--path", ".", "--task", task["taskId"], label="run start"
    )
    run_id = started.get("executionId", "") if isinstance(started, dict) else ""
    waits: list[str] = []
    code = simulated_person(h, ws, run_id, task["taskId"], run_dir, waits, code)
    record["runExitCode"] = code
    record["waits"] = waits
    _, status, _ = h.run(ws, "status", "--path", ".", "--run", run_id, label="status")
    execution = status.get("execution", {}) if isinstance(status, dict) else {}
    gate = (status.get("gate") or {}) if isinstance(status, dict) else {}
    record.update(
        {
            "runStatus": execution.get("status"),
            "phase": execution.get("currentPhase"),
            "gate": gate.get("status"),
            "gateReasons": gate.get("reasonCodes", []),
        }
    )
    _, shown, _ = h.run(
        ws, "verification", "show", "--path", ".", "--run", run_id, label="verification show"
    )
    certification = (shown.get("certification") or {}) if isinstance(shown, dict) else {}
    record["certification"] = certification.get("status")
    record["criteria"] = {
        str(item.get("criterionId")): {
            k: item.get(k)
            for k in ("status", "required", "reached", "level", "evidence")
            if k in item
        }
        for item in certification.get("criteria", [])
        if isinstance(item, dict)
    }
    record["preflight"] = (
        (shown.get("preflight") or {}).get("status") if isinstance(shown, dict) else None
    )
    _, findings, _ = h.run(
        ws, "findings", "list", "--path", ".", "--run", run_id, label="findings list"
    )
    findings = findings if isinstance(findings, list) else []
    record["findings"] = sorted({f"{f['severity']}:{f['ruleId']}" for f in findings})
    record["mutationFindings"] = sorted(
        {f["ruleId"] for f in findings if f["ruleId"] in MUTATION_RULES}
    )
    record["testQualityFindings"] = sorted(
        {
            f["ruleId"]
            for f in findings
            if f["ruleId"].startswith("tests.") and f["ruleId"] not in MUTATION_RULES
        }
    )
    record["certificationFindings"] = sorted(
        {f["ruleId"] for f in findings if f["ruleId"].startswith("certification.")}
    )
    record["oracle"] = oracle(
        case, corpus, files, run_dir / "oracle", str(args.project_venv / "bin" / "python")
    )
    record["defectiveObserved"] = record["oracle"]["failed"] > 0
    record["truthConsistent"] = record["defectiveObserved"] == bool(case["truth"]["defective"])
    record["signals"] = {
        "certification": record["certification"] in FLAGGED_CERTIFICATION
        or bool(record["certificationFindings"]),
        "mutation": bool(record["mutationFindings"]),
        "testQuality": bool(record["testQualityFindings"]),
        "gateNotPassed": record["gate"] != "PASSED",
    }
    record["signals"]["any"] = any(record["signals"].values())
    record["steps"] = h.steps
    print(
        case["id"],
        rep,
        record["runStatus"],
        record["phase"],
        record["gate"],
        record["certification"],
        record["mutationFindings"],
        "defective" if record["defectiveObserved"] else "correct",
        flush=True,
    )
    return record


def rates(records: list[dict[str, Any]], signal: str, truth: str) -> dict[str, Any]:
    tp = fn = fp = tn = 0
    for record in records:
        positive = (
            bool(record["truth"]["defective"])
            if truth == "defective"
            else not record["truth"]["evidenceAdequate"]
        )
        flagged = record["signals"][signal]
        if positive and flagged:
            tp += 1
        elif positive:
            fn += 1
        elif flagged:
            fp += 1
        else:
            tn += 1
    return {
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "sensitivity": round(tp / (tp + fn), 3) if tp + fn else None,
        "specificity": round(tn / (tn + fp), 3) if tn + fp else None,
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    first = [r for r in records if r["rep"] == 1]
    by_case: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_case.setdefault(record["case"], []).append(record)

    def outcome(record: dict[str, Any]) -> list[Any]:
        return [
            record["runStatus"],
            record["phase"],
            record["gate"],
            record["certification"],
            record["mutationFindings"],
            record["testQualityFindings"],
            record["defectiveObserved"],
        ]

    return {
        "cases": len(first),
        "defective": sum(1 for r in first if r["truth"]["defective"]),
        "inadequateEvidence": sum(1 for r in first if not r["truth"]["evidenceAdequate"]),
        "truthConsistent": sum(1 for r in first if r["truthConsistent"]),
        "identicalAcrossRepetitions": {
            case: len({json.dumps(outcome(r)) for r in items}) == 1
            for case, items in by_case.items()
        },
        "vsDefective": {
            signal: rates(first, signal, "defective")
            for signal in ("certification", "mutation", "testQuality", "gateNotPassed", "any")
        },
        "vsInadequateEvidence": {
            signal: rates(first, signal, "evidence")
            for signal in ("certification", "mutation", "testQuality", "gateNotPassed", "any")
        },
        "table": [
            {
                "case": r["case"],
                "defective": r["truth"]["defective"],
                "evidenceAdequate": r["truth"]["evidenceAdequate"],
                "runStatus": r["runStatus"],
                "phase": r["phase"],
                "gate": r["gate"],
                "certification": r["certification"],
                "criteria": {k: v.get("status") for k, v in r["criteria"].items()},
                "mutation": r["mutationFindings"],
                "testQuality": r["testQualityFindings"],
                "oracle": f"{r['oracle']['passed']}/{r['oracle']['passed'] + r['oracle']['failed']}",
            }
            for r in first
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--project-venv", type=Path, default=Path(sys.executable).parent.parent)
    parser.add_argument("--only", default="")
    parser.add_argument("--mutation-mode", default="", help="override verification.mutation.mode")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.work = (args.work or args.out / ".work").resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    corpus, snippets = load_corpus()
    records_path = args.out / "ladder-corpus.jsonl"
    if not args.summarize_only:
        cases = [c for c in corpus["cases"] if not args.only or c["id"] in args.only.split(",")]
        write_json(
            args.out / "environment.json",
            environment(
                args.harness,
                args.wheel,
                {
                    "suite": "N01-a ladder corpus",
                    "mutationMode": args.mutation_mode or "as written by harness init",
                    "cases": [c["id"] for c in cases],
                    "reps": args.reps,
                    "projectVenv": str(args.project_venv),
                },
            ),
        )
        records = [
            run_case(case, corpus, snippets, args, rep)
            for rep in range(1, args.reps + 1)
            for case in cases
        ]
        write_jsonl(records_path, records)
    records = [json.loads(line) for line in records_path.read_text().splitlines() if line.strip()]
    write_json(args.out / "ladder-summary.json", summarize(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
