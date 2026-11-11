#!/usr/bin/env python3
"""H45: false positives and review load on clean changes (``CATALOGO.md`` §2.8,
``PREREGISTRO.md``). No model is called.

Thirty-two clean changes, each run on a fresh copy of its own baseline with the fixture provider
of the interception suites (``interception_agent.py`` writes the change as it is), under ``H``
(``harness init``) and ``H-core`` (the 1.0.0 key set):

* the 10 clean cases of H01, the 8 decoys (clean) of H03 and the 5 clean cases of H05, with their
  baselines and tasks;
* the 2 reference solutions of ``evaluation/reference/`` (greenfield-shipping and
  greenfield-alerts) on their fixtures, with the task of ``evaluation/tasks/`` as it is and, as
  the change's visible tests, the tests of ``evaluation/hidden/`` of the scenario;
* the 7 clean cases of the review corpus of N02-a (``evaluation/corpus/review/cases.yaml``).

For every run: findings by severity and rule, blocking findings (severity in
``policies.findingBlockSeverities``), whether the run was blocked by any cause (the definition
of H01-H05), the simulated person's waits and the risks of the decision brief
(``review --run --json``). The criterion is reported as is: 0 blocks on the reference solutions
(condition H).

Usage:
  python evaluation/deterministic/h45_clean_changes.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only ref-shipping,...]
      [--conditions harness,harness-core] [--reps 1] [--summarize-only]

Writes ``DIR/h45-clean-changes.jsonl``, ``DIR/h45-clean-changes-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import h01_weakened_tests  # noqa: E402
import h03_secrets  # noqa: E402
import h05_dependencies  # noqa: E402
import interception  # noqa: E402
from common import EVALUATION, Harness  # noqa: E402
from resource_support import Utf8Harness, main, proportion  # noqa: E402

CONDITIONS = {"harness": interception.init_config, "harness-core": interception.core_condition}
REVIEW = EVALUATION / "corpus" / "review"
REFERENCES = {
    "ref-shipping": ("greenfield-shipping", "shipping"),
    "ref-alerts": ("greenfield-alerts", "alerts"),
}


def brief(
    h: Harness, ws: Path, run_id: str, status: dict[str, Any], case: dict[str, Any]
) -> dict[str, Any]:
    """The review load a person sees: the risks of the decision brief."""
    del status, case
    # Decoded as UTF-8: the brief may carry text the Windows code page cannot decode.
    reader = Utf8Harness(h.executable, h.env)
    code, shown, _ = reader.run(ws, "review", "--path", ".", "--run", run_id, "--json")
    h.steps.extend(reader.steps)
    risks = shown.get("risks") if isinstance(shown, dict) else None
    return {
        "reviewExitCode": code,
        "briefRisks": len(risks) if isinstance(risks, list) else None,
        "briefRiskRules": sorted(
            {
                str(item.get("ruleId") or item.get("rule"))
                for item in risks
                if isinstance(item, dict)
            }
        )
        if isinstance(risks, list)
        else [],
    }


def never(case: dict[str, Any], findings: list[dict[str, Any]]) -> bool:
    del case, findings
    return False


def from_suite(source: Any, kinds: tuple[str, ...]) -> interception.Suite:
    """The clean cases of an interception suite, with its baseline and tasks."""
    cases = [
        {
            **case,
            "id": f"{source.suite_id.lower()}-{case['id']}",
            "source": source.suite_id,
            "originalId": case["id"],
            "kind": "clean",
        }
        for case in source.cases
        if case["kind"] in kinds
    ]

    def task_for(case: dict[str, Any], condition: str) -> dict[str, Any]:
        # The source's task for its own case and its plain harness condition.
        original = {**case, "id": case["originalId"]}
        task = source.task_for(original, "harness")
        task["taskId"] = "task_h45_" + case["id"].replace("-", "_")
        return task

    return interception.Suite(
        suite_id="H45",
        title=f"clean changes of {source.suite_id}",
        build_baseline=source.build_baseline,
        cases=cases,
        conditions=dict(CONDITIONS),
        task_for=task_for,
        detect=never,
        rules=(),
        relevant=(),
        after_decision=brief,
    )


# ----- the reference solutions -----------------------------------------------------------------
def reference_case(case_id: str) -> dict[str, Any]:
    scenario, package = REFERENCES[case_id]
    code = (EVALUATION / "reference" / scenario / package / "__init__.py").read_text(
        encoding="utf-8"
    )
    hidden = next((EVALUATION / "hidden" / scenario).glob("test_*.py"))
    return {
        "id": case_id,
        "source": "reference",
        "scenario": scenario,
        "kind": "clean",
        "write": {
            f"src/{package}/__init__.py": code,
            f"tests/test_{package}.py": hidden.read_text(encoding="utf-8"),
        },
        "description": f"reference solution of {scenario} with the hidden tests as visible tests",
    }


def reference_suite(case_id: str) -> interception.Suite:
    scenario, _ = REFERENCES[case_id]
    fixture = EVALUATION / "fixtures" / scenario

    def build(root: Path) -> None:
        for item in fixture.iterdir():
            target = ".gitignore" if item.name == "gitignore.template" else item.name
            shutil.copy2(item, root / target)

    def task_for(case: dict[str, Any], condition: str) -> dict[str, Any]:
        del case, condition
        return yaml.safe_load(
            (EVALUATION / "tasks" / f"{scenario}.yaml").read_text(encoding="utf-8")
        )

    return interception.Suite(
        suite_id="H45",
        title=f"reference solution of {scenario}",
        build_baseline=build,
        cases=[reference_case(case_id)],
        conditions=dict(CONDITIONS),
        task_for=task_for,
        detect=never,
        rules=(),
        relevant=(),
        after_decision=brief,
    )


# ----- the review corpus -----------------------------------------------------------------------
def review_target(relative: str) -> str:
    return relative.replace("dot-github/", ".github/", 1)


def review_baseline(root: Path) -> None:
    shutil.copytree(REVIEW / "baseline", root, dirs_exist_ok=True)
    (root / "gitignore.template").rename(root / ".gitignore")
    (root / "dot-github").rename(root / ".github")


def review_change(case: dict[str, Any]) -> dict[str, str]:
    """The edits of a corpus case (``append``, ``write``, ``replace``) as whole files."""
    files: dict[str, str] = {}

    def current(relative: str) -> str:
        if relative in files:
            return files[relative]
        source = REVIEW / "baseline" / relative
        return source.read_text(encoding="utf-8") if source.exists() else ""

    for relative, text in (case.get("write") or {}).items():
        files[relative] = text
    for relative, text in (case.get("append") or {}).items():
        files[relative] = current(relative) + text
    for item in case.get("replace") or []:
        before = current(item["path"])
        if item["old"] not in before:
            raise ValueError(f"{case['id']}: replace target not found in {item['path']}")
        files[item["path"]] = before.replace(item["old"], item["new"], 1)
    return {review_target(relative): text for relative, text in files.items()}


def review_suite() -> interception.Suite:
    corpus = yaml.safe_load((REVIEW / "cases.yaml").read_text(encoding="utf-8"))
    cases = [
        {
            "id": f"review-{case['id']}",
            "source": "N02-a",
            "originalId": case["id"],
            "kind": "clean",
            "write": review_change(case),
            "description": f"clean case {case['id']} of the review corpus",
        }
        for case in corpus["cases"]
        if not case.get("truth")
    ]

    def task_for(case: dict[str, Any], condition: str) -> dict[str, Any]:
        del condition
        return {
            "taskId": "task_h45_" + case["id"].replace("-", "_"),
            "title": f"Billing change {case['originalId']}",
            "intent": f"Make the change {case['originalId']} of the billing project.",
            "acceptanceCriteria": [
                {"criterionId": "ac_tests", "text": "python -m pytest -q passes."}
            ],
        }

    return interception.Suite(
        suite_id="H45",
        title="clean cases of the review corpus",
        build_baseline=review_baseline,
        cases=cases,
        conditions=dict(CONDITIONS),
        task_for=task_for,
        detect=never,
        rules=(),
        relevant=(),
        after_decision=brief,
    )


SUITES = [
    from_suite(h01_weakened_tests.SUITE, ("clean",)),
    from_suite(h03_secrets.SUITE, ("clean",)),
    from_suite(h05_dependencies.SUITE, ("clean",)),
    reference_suite("ref-shipping"),
    reference_suite("ref-alerts"),
    review_suite(),
]
SUITE_OF = {case["id"]: suite for suite in SUITES for case in suite.cases}
CASES = [case for suite in SUITES for case in suite.cases]


def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    record = interception.run_one(SUITE_OF[case["id"]], case, condition, args, rep)
    record["source"] = case["source"]
    record["brief"] = f"blocked={record.get('blocked')} blocking={record.get('blockingFindings')}"
    if "apparatusError" in record:
        raise RuntimeError(record["apparatusError"])
    return record


def stats(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
        "total": sum(values),
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    by_condition: dict[str, Any] = {}
    for condition in CONDITIONS:
        rows = [r for r in ok if r["condition"] == condition]
        if not rows:
            continue
        noise: dict[str, int] = {}
        for r in rows:
            for key in {f"{f['severity']}:{f['rule']}" for f in r.get("findings") or []}:
                noise[key] = noise.get(key, 0) + 1
        by_condition[condition] = {
            "changes": len(rows),
            "findingsPerChange": stats([len(r.get("findings") or []) for r in rows]),
            "blockingPerChange": stats(
                [
                    sum(
                        1
                        for f in r.get("findings") or []
                        if f["severity"] in interception.BLOCK_SEVERITIES
                    )
                    for r in rows
                ]
            ),
            "withBlockingFinding": proportion(
                sum(r["hasBlockingFinding"] for r in rows), len(rows)
            ),
            "blockedAnyCause": proportion(sum(r["blocked"] for r in rows), len(rows)),
            "briefRisksPerChange": stats(
                [r["briefRisks"] for r in rows if isinstance(r.get("briefRisks"), int)]
            ),
            "waitsPerChange": stats([len(r.get("waits") or []) for r in rows]),
            "noisiestRules": dict(sorted(noise.items(), key=lambda item: (-item[1], item[0]))),
            "bySource": {
                source: {
                    "changes": sum(1 for r in rows if r["source"] == source),
                    "withBlockingFinding": sum(
                        r["hasBlockingFinding"] for r in rows if r["source"] == source
                    ),
                    "blocked": sum(r["blocked"] for r in rows if r["source"] == source),
                }
                for source in sorted({r["source"] for r in rows})
            },
            "cases": {
                r["case"]: {
                    "gate": r.get("gate"),
                    "phase": r.get("phase"),
                    "blocking": r.get("blockingFindings"),
                    "blocked": r.get("blocked"),
                }
                for r in rows
            },
        }
    references = [r for r in ok if r["source"] == "reference" and r["condition"] == "harness"]
    blocked_references = [r["case"] for r in references if r["blocked"]]
    summary = {
        "suite": "H45",
        "title": "false positives and review load on clean changes",
        "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION (a "
        "declared simulation); nobody decides at DECISION",
        "corpus": {
            source: sum(1 for c in CASES if c["source"] == source)
            for source in sorted({c["source"] for c in CASES})
        },
        "byCondition": by_condition,
    }
    summary["criteria"] = {
        "zeroBlocksOnReferenceSolutions": {
            "value": f"{len(blocked_references)} of {len(references)} reference solutions blocked "
            f"under harness: {blocked_references}",
            "met": len(references) == len(REFERENCES) and not blocked_references,
        },
        "note": "Pre-registered in PREREGISTRO.md (H45): reported as is; the threshold applies "
        "to the reference solutions under harness.",
    }
    return summary


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H45",
            title="false positives and review load on clean changes",
            stem="h45-clean-changes",
            cases=CASES,
            conditions_of=lambda case: list(CONDITIONS),
            all_conditions=list(CONDITIONS),
            runner=runner,
            summarize=summarize,
        )
    )
