#!/usr/bin/env python3
"""P24: the 2.0.0 INTENT rules applied to every task of the experiments, without agents.

Every task the evaluations sent (or would send) to the harness is loaded the way
``harness task create`` loads a task file under ``intake.criteriaPolicy: enforce``
(``governed_harness.application.task_loader.load_task_file``) and assessed with the
deterministic INTENT rules (``governed_harness.intake.clarification.assess_intent``: C0, C1,
C2, C3, T1). Free-text prompts (the one-line prompts and the bug reports of the iterative block)
are assessed twice: as ``harness do TEXT`` builds the task (the text is the intent and the
criterion, ``application.friction.task_from_text``) and as a task file with only the text as
intent (no criteria: rule C0). The rules that need an agent (A1, A2, A3) or a workspace (P1)
are not applied; no model is called.

Sources: ``evaluation/tasks`` (full, poor and casual), the five increments and the bug-report
fix tasks of ``evaluation/longitudinal`` (built as ``run_session.py`` builds them), and the
rides and large-project tasks (built by ``rides/run_rides.py`` and ``large/run_large.py``).

The script runs its assessment with the harness's interpreter (``--harness-python``, default:
``python`` next to the harness executable), so it reads the rules of the installed harness.

Usage:
  python evaluation/deterministic/intent_equivalence.py --out DIR [--harness PATH]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent


def load_module(name: str, path: Path) -> Any:
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def experiment_tasks() -> list[dict[str, Any]]:
    """(source, id, kind, payload): kind 'file' (a task dict) or 'text' (a free-text prompt)."""
    import yaml

    items: list[dict[str, Any]] = []
    for path in sorted((EVALUATION / "tasks").glob("*.yaml")):
        items.append(
            {
                "source": "evaluation/tasks",
                "id": path.stem,
                "kind": "file",
                "task": yaml.safe_load(path.read_text()),
            }
        )
    for path in sorted((EVALUATION / "tasks").glob("*-casual.txt")):
        items.append(
            {
                "source": "evaluation/tasks",
                "id": path.stem,
                "kind": "text",
                "text": path.read_text().strip(),
            }
        )
    increments = yaml.safe_load((EVALUATION / "longitudinal" / "increments.yaml").read_text())
    for increment in increments:
        items.append(
            {
                "source": "longitudinal/increments",
                "id": f"{increment['id']}-task",
                "kind": "file",
                "task": dict(increment["task"]),
            }
        )
        items.append(
            {
                "source": "longitudinal/increments",
                "id": f"{increment['id']}-casual",
                "kind": "text",
                "text": increment["casual"],
            }
        )
    reports = yaml.safe_load((EVALUATION / "longitudinal" / "bug_reports.yaml").read_text())
    for name, report in reports.items():
        # The fix task and the casual fix prompt of run_session.py for a single report.
        items.append(
            {
                "source": "longitudinal/bug_reports",
                "id": f"fix-{name}",
                "kind": "file",
                "task": {
                    "title": "Fix reported defects after I1 (round 1)",
                    "intent": "Fix the reported defects without breaking other behavior, and add tests for them.",
                    "requirements": [report],
                    "acceptanceCriteria": [f"Fixed: {report}", "All tests in tests/ pass."],
                    "constraints": ["Use only the Python standard library."],
                },
            }
        )
        items.append(
            {
                "source": "longitudinal/bug_reports",
                "id": f"fix-{name}-casual",
                "kind": "text",
                "text": "some things are broken:\n- " + report + "\nfix them",
            }
        )
    for label, folder, script in (
        ("rides", "rides", "run_rides.py"),
        ("large", "large", "run_large.py"),
    ):
        module = load_module(f"{label}_runner", EVALUATION / folder / script)
        for level in ("one_line", "paragraph", "requirements"):
            task = module.whole_task(level)
            items.append({"source": f"{label}/whole", "id": level, "kind": "file", "task": task})
        prompts = getattr(module, "PROMPTS", None) or module.SPEC
        items.append(
            {
                "source": f"{label}/prompts",
                "id": "one_line-text",
                "kind": "text",
                "text": prompts["one_line"],
            }
        )
        for step in prompts.get("steps") or prompts.get("increments") or []:
            items.append(
                {
                    "source": f"{label}/steps",
                    "id": step.get("id"),
                    "kind": "file",
                    "task": module.step_task(step),
                }
            )
    return items


def assess(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import yaml

    import governed_harness
    from governed_harness.application.task_loader import load_task_file
    from governed_harness.intake.clarification import assess_intent

    try:
        from governed_harness.application.friction import task_from_text
    except ImportError:  # a harness without `harness do`
        task_from_text = None
    results = []
    with tempfile.TemporaryDirectory() as scratch:
        for index, item in enumerate(items):
            variants: list[tuple[str, Any]] = []
            if item["kind"] == "file":
                task = {key: value for key, value in item["task"].items() if key != "taskId"}
                variants.append(("task file", task))
            else:
                if task_from_text is not None:
                    variants.append(
                        (
                            "harness do",
                            task_from_text(
                                item["text"], project_id="p24", task_id=f"task_p24_{index}"
                            ),
                        )
                    )
                first = item["text"].splitlines()[0][:300]
                variants.append(
                    ("task file without criteria", {"title": first, "intent": item["text"]})
                )
            for label, value in variants:
                if isinstance(value, dict):
                    path = Path(scratch) / f"task-{index}-{len(results)}.yaml"
                    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True))
                    try:
                        loaded = load_task_file(path, project_id="p24", criteria_policy="enforce")
                    except Exception as error:  # noqa: BLE001 - recorded as the outcome
                        results.append(
                            {
                                **_head(item),
                                "as": label,
                                "error": f"{type(error).__name__}: {error}",
                            }
                        )
                        continue
                else:
                    loaded = value
                questions = assess_intent(loaded)
                rules = Counter(q.rule_id for q in questions)
                results.append(
                    {
                        **_head(item),
                        "as": label,
                        "criteria": len(loaded.acceptance_criteria),
                        "requirements": len(loaded.requirements),
                        "criteriaPending": bool(getattr(loaded, "criteria_pending", False)),
                        "questions": len(questions),
                        "byRule": dict(sorted(rules.items())),
                        "questionTargets": [f"{q.rule_id}:{q.target}" for q in questions],
                    }
                )
    return [{"harnessVersion": governed_harness.__version__}, *results]


def _head(item: dict[str, Any]) -> dict[str, Any]:
    return {"source": item["source"], "id": item["id"], "kind": item["kind"]}


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, dict[str, Any]] = {}
    for record in records:
        key = f"{record['source']} ({record['as']})"
        bucket = by_source.setdefault(
            key, {"tasks": 0, "withQuestions": 0, "questions": 0, "byRule": Counter(), "errors": 0}
        )
        bucket["tasks"] += 1
        if "error" in record:
            bucket["errors"] += 1
            continue
        bucket["withQuestions"] += int(record["questions"] > 0)
        bucket["questions"] += record["questions"]
        bucket["byRule"].update(record["byRule"])
    for bucket in by_source.values():
        bucket["byRule"] = dict(sorted(bucket["byRule"].items()))
    return {
        "rulesApplied": ["C0", "C1", "C2", "C3", "T1"],
        "rulesNotApplied": {
            "A1": "needs an agent",
            "A2": "applies to answers and needs the workspace",
            "A3": "needs an agent (locate)",
            "P1": "needs the workspace",
        },
        "tasks": len(records),
        "withQuestions": sum(1 for r in records if r.get("questions")),
        "bySource": by_source,
        "withQuestionsList": [
            f"{r['source']}/{r['id']} ({r['as']}): {r['byRule']}"
            for r in records
            if r.get("questions")
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sys.path.insert(0, str(HERE))
    from common import add_common_arguments, environment, write_json, write_jsonl

    add_common_arguments(parser)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--harness-python", default="")
    parser.add_argument("--inner", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inner:
        items = experiment_tasks()
        args.inner.write_text(json.dumps(assess(items)))
        return 0
    args.out = args.out.resolve()
    python = args.harness_python or str(Path(args.harness).resolve().parent / "python")
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(scratch) / "assessed.json"
        proc = subprocess.run(
            [python, __file__, "--out", str(args.out), "--inner", str(target)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout + proc.stderr)
            return proc.returncode
        assessed = json.loads(target.read_text())
    version, records = assessed[0], assessed[1:]
    write_json(
        args.out / "environment.json",
        environment(
            args.harness,
            args.wheel,
            {
                "suite": "P24 INTENT equivalence",
                "assessedWith": python,
                "governedHarnessVersion": version["harnessVersion"],
            },
        ),
    )
    write_jsonl(args.out / "intent-equivalence.jsonl", records)
    write_json(args.out / "intent-equivalence-summary.json", summarize(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
