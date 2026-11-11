#!/usr/bin/env python3
"""H35: light mutation off/warn/enforce (``CATALOGO.md`` §2.6, ``PREREGISTRO.md``). No model.

A small ``metrics`` package written by the suite (ten functions in ``src/metrics/core.py`` and
baseline tests that pass before and after every change). Each of the 40 cases changes the body of
one function (one hunk, same signature) and adds ``tests/test_<function>_change.py``, which runs
the new branch, so the changed lines are covered 100 %:

* 30 weak cases: 10 functions x 3 assertion styles that pass on the old code and on the new one
  (``type``: only the type of the result; ``loose``: a relation both values meet;
  ``unasserted``: the new branch is called, its result not asserted, and another input that the
  change does not touch is asserted);
* 10 strong controls: the same call with the exact new value, which fails without the change.

Conditions (``maxHunks`` 10, ``maxSeconds`` 300, the values of ``harness init``):

* ``coverage-only``: init + ``friction.fastLane.mode: off`` + ``verification.mutation.mode: off``
  (``testQuality.diffCoverage: 80`` of init stays: change coverage alone);
* ``mutation-warn``: init + ``friction.fastLane.mode: off`` (mutation ``warn``, as init);
* ``mutation-enforce``: init + ``friction.fastLane.mode: off`` + mutation ``enforce``;
* ``init-as-written`` (context): init as it is; a size S task takes the fast lane, whose
  ``friction.fastLane.skip`` leaves the mutation out.

Every record holds the findings, the mutation report of the run (``mutation-report`` artifact:
hunks, hunks left out, test-file classes, elapsed seconds or the reason it did not run), the
change coverage of the test-quality report and whether the fast lane skipped the mutation.

Usage:
  python evaluation/deterministic/h35_light_mutation.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only clamp-type,...]
      [--conditions mutation-warn,...] [--reps 1] [--summarize-only]

Writes ``DIR/h35-light-mutation.jsonl``, ``DIR/h35-light-mutation-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import interception  # noqa: E402
from common import Harness  # noqa: E402
from resource_support import main, proportion  # noqa: E402

MUTATION_RULES = ("tests.change-not-exercised", "tests.weak")
FP_RULES = (*MUTATION_RULES, "tests.broken")
MAX_SECONDS = 300
FAST_LANE_CANDIDATE_SECONDS = 60
SOURCE = "src/metrics/core.py"

# ----- the project -----------------------------------------------------------------------------
# name -> (baseline body line, changed body lines, branch call, new value repr, type,
#          loose assertion on r, neutral call, neutral value repr)
FUNCTIONS: dict[str, dict[str, str]] = {
    "clamp_percent": {
        "signature": "def clamp_percent(value):",
        "before": "    return value\n",
        "after": "    return max(0, min(100, value))\n",
        "call": "clamp_percent(150)",
        "new": "100",
        "type": "int",
        "loose": "r >= 0",
        "neutral": "clamp_percent(50)",
        "neutralValue": "50",
    },
    "round_money": {
        "signature": "def round_money(amount):",
        "before": "    return amount\n",
        "after": "    return round(amount, 2)\n",
        "call": "round_money(1.239)",
        "new": "1.24",
        "type": "float",
        "loose": "abs(r - 1.239) < 0.01",
        "neutral": "round_money(2.5)",
        "neutralValue": "2.5",
    },
    "normalize_name": {
        "signature": "def normalize_name(name):",
        "before": "    return name\n",
        "after": "    return name.strip().title()\n",
        "call": 'normalize_name("  ada lovelace ")',
        "new": '"Ada Lovelace"',
        "type": "str",
        "loose": '"ada" in r.lower()',
        "neutral": 'normalize_name("Ada")',
        "neutralValue": '"Ada"',
    },
    "safe_ratio": {
        "signature": "def safe_ratio(part, whole):",
        "before": "    return part / whole if whole else 0.0\n",
        "after": "    return round(part / whole, 3) if whole else 0.0\n",
        "call": "safe_ratio(1, 3)",
        "new": "0.333",
        "type": "float",
        "loose": "0 < r < 1",
        "neutral": "safe_ratio(1, 0)",
        "neutralValue": "0.0",
    },
    "bucket": {
        "signature": "def bucket(score):",
        "before": '    return "low" if score < 50 else "high"\n',
        "after": (
            '    if 50 <= score < 80:\n        return "mid"\n'
            '    return "low" if score < 50 else "high"\n'
        ),
        "call": "bucket(60)",
        "new": '"mid"',
        "type": "str",
        "loose": 'r in {"low", "mid", "high"}',
        "neutral": "bucket(10)",
        "neutralValue": '"low"',
    },
    "discount": {
        "signature": "def discount(total):",
        "before": "    return total\n",
        "after": "    return total * 0.9 if total >= 100 else total\n",
        "call": "discount(200)",
        "new": "180.0",
        "type": "(int, float)",
        "loose": "r <= 200",
        "neutral": "discount(50)",
        "neutralValue": "50",
    },
    "dedupe": {
        "signature": "def dedupe(items):",
        "before": "    return list(items)\n",
        "after": "    return list(dict.fromkeys(items))\n",
        "call": "dedupe([1, 1, 2])",
        "new": "[1, 2]",
        "type": "list",
        "loose": "set(r) == {1, 2}",
        "neutral": "dedupe([])",
        "neutralValue": "[]",
    },
    "tax": {
        "signature": "def tax(amount):",
        "before": "    return amount * 0.19\n",
        "after": "    return max(amount, 0) * 0.19\n",
        "call": "tax(-10)",
        "new": "0.0",
        "type": "float",
        "loose": "r <= 0",
        "neutral": "tax(100)",
        "neutralValue": "pytest.approx(19)",
    },
    "initials": {
        "signature": "def initials(name):",
        "before": "    return name[:1]\n",
        "after": '    return "".join(part[0] for part in name.split()).upper()\n',
        "call": 'initials("ada lovelace")',
        "new": '"AL"',
        "type": "str",
        "loose": "len(r) >= 1",
        "neutral": 'initials("A")',
        "neutralValue": '"A"',
    },
    "parse_flag": {
        "signature": "def parse_flag(text):",
        "before": '    return text == "yes"\n',
        "after": '    return text.strip().lower() in {"yes", "true", "1"}\n',
        "call": 'parse_flag(" TRUE ")',
        "new": "True",
        "type": "bool",
        "loose": "r in (True, False)",
        "neutral": 'parse_flag("yes")',
        "neutralValue": "True",
    },
}
BASELINE_TESTS = """\
import pytest

from metrics.core import (
    bucket,
    clamp_percent,
    dedupe,
    discount,
    initials,
    normalize_name,
    parse_flag,
    round_money,
    safe_ratio,
    tax,
)


def test_clamp_percent_in_range():
    assert clamp_percent(50) == 50


def test_round_money_exact():
    assert round_money(2.5) == 2.5


def test_normalize_name_already_clean():
    assert normalize_name("Ada") == "Ada"


def test_safe_ratio():
    assert safe_ratio(1, 2) == 0.5
    assert safe_ratio(1, 0) == 0.0


def test_bucket_extremes():
    assert bucket(10) == "low"
    assert bucket(90) == "high"


def test_discount_small_total():
    assert discount(50) == 50


def test_dedupe_distinct():
    assert dedupe([1, 2]) == [1, 2]


def test_tax_positive():
    assert tax(100) == pytest.approx(19)


def test_initials_single_letter():
    assert initials("A") == "A"


def test_parse_flag_yes():
    assert parse_flag("yes") is True
"""


def source(changed: str | None = None) -> str:
    """``core.py`` with every function as on the baseline, or ``changed`` with its new body."""
    parts = ['"""Small metrics helpers."""\n']
    for name, item in FUNCTIONS.items():
        body = item["after"] if name == changed else item["before"]
        parts.append(f"\n\n{item['signature']}\n{body}")
    return "".join(parts)


def build_baseline(root: Path) -> None:
    interception.write_files(
        root,
        {
            SOURCE: source(),
            "src/metrics/__init__.py": "",
            "tests/test_core.py": BASELINE_TESTS,
            "pyproject.toml": (
                '[project]\nname = "metrics"\nversion = "0.1.0"\n\n'
                '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
            ),
            "README.md": "# metrics\n\nSmall metrics helpers.\n",
            ".gitignore": ".harness/\n__pycache__/\n.pytest_cache/\n.coverage*\n",
        },
    )


def test_file(name: str, style: str) -> str:
    item = FUNCTIONS[name]
    header = "import pytest\n\n" if "pytest." in item["neutralValue"] else ""
    header += f"from metrics.core import {name}\n\n\n"
    test = f"def test_ac_{name}_{style}():\n"
    if style == "type":
        body = f"    assert isinstance({item['call']}, {item['type']})\n"
    elif style == "loose":
        body = f"    r = {item['call']}\n    assert {item['loose']}\n"
    elif style == "unasserted":
        body = f"    {item['call']}\n    assert {item['neutral']} == {item['neutralValue']}\n"
    else:
        body = f"    assert {item['call']} == {item['new']}\n"
    return header + test + body


def case(name: str, style: str) -> dict[str, Any]:
    weak = style != "strong"
    return {
        "id": f"{name.replace('_', '-')}-{style}",
        "kind": "seeded" if weak else "clean",
        "group": style,
        "function": name,
        "style": style,
        "rule": None,
        "description": (
            f"{name} changes; the new test covers the branch with a {style} assertion"
            if weak
            else f"{name} changes; the new test asserts the exact new value (control)"
        ),
        "write": {
            SOURCE: source(name),
            f"tests/test_{name}_change.py": test_file(name, style),
        },
    }


STYLES = ("type", "loose", "unasserted", "strong")
CASES = [case(name, style) for style in STYLES for name in FUNCTIONS]


def task_for(item: dict[str, Any], condition: str) -> dict[str, Any]:
    del condition
    name = item["function"]
    return {
        "taskId": "task_h35_" + item["id"].replace("-", "_"),
        "title": f"Change {name}",
        "intent": f"Change the behaviour of metrics.core.{name} as described.",
        "acceptanceCriteria": [
            {
                "criterionId": f"ac_{name}",
                "text": f"{FUNCTIONS[name]['call']} returns {FUNCTIONS[name]['new']}.",
            }
        ],
        "metadata": {"ownedPaths": [SOURCE, f"tests/test_{name}_change.py"]},
    }


def detect(item: dict[str, Any], findings: list[dict[str, Any]]) -> bool:
    del item
    return any(f["rule"] in MUTATION_RULES for f in findings)


# ----- conditions ------------------------------------------------------------------------------
def _full_lane(config: dict[str, Any]) -> dict[str, Any]:
    config.setdefault("friction", {}).setdefault("fastLane", {})["mode"] = "off"
    return config


def _mutation(mode: str) -> interception.ConfigBuilder:
    def builder(config: dict[str, Any]) -> dict[str, Any]:
        config = _full_lane(config)
        mutation = config.setdefault("verification", {}).setdefault("mutation", {})
        mutation["mode"] = mode
        mutation.setdefault("maxHunks", 10)
        mutation.setdefault("maxSeconds", MAX_SECONDS)
        return config

    return builder


CONDITIONS: dict[str, interception.ConfigBuilder | None] = {
    "coverage-only": _mutation("off"),
    "mutation-warn": _mutation("warn"),
    "mutation-enforce": _mutation("enforce"),
    "init-as-written": interception.init_config,
}
MEASURED = ("coverage-only", "mutation-warn", "mutation-enforce")


# ----- what the run recorded -------------------------------------------------------------------
def _artifacts(root: Path, run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The mutation reports of the run (oldest first) and the test-quality reports under
    ``root`` (``evidence/artifact_store.py``: ``metadata/<hex>.json`` and ``blobs/sha256``)."""
    mutation: list[tuple[float, dict[str, Any]]] = []
    quality: list[tuple[float, dict[str, Any]]] = []
    for meta in root.rglob("metadata/*.json"):
        try:
            descriptor = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        info = descriptor.get("metadata") or {}
        kind = info.get("kind")
        wanted = (kind == "mutation-report" and info.get("executionId") == run_id) or (
            kind == "verification-check" and info.get("validatorId") == "harness.test-quality"
        )
        if not wanted:
            continue
        digest = meta.stem
        blob = meta.parent.parent / "blobs" / "sha256" / digest[:2] / digest[2:]
        try:
            value = json.loads(blob.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        (mutation if kind == "mutation-report" else quality).append((meta.stat().st_mtime, value))
    return [v for _, v in sorted(mutation, key=lambda x: x[0])], [
        v for _, v in sorted(quality, key=lambda x: x[0])
    ]


def _skipped_steps(database: Path, run_id: str) -> list[str]:
    if not database.exists():
        return []
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT payload_json FROM events WHERE execution_id=? AND event_type=? "
            "ORDER BY execution_sequence",
            (run_id, "lane.step.skipped"),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    return [str(json.loads(p).get("step")) for (p,) in rows]


def after(
    h: Harness, ws: Path, run_id: str, status: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    del status, item
    from common import state_database

    reports, quality = _artifacts(ws.parent, run_id)
    last = reports[-1] if reports else None
    coverage = [q.get("diffCoverage") for q in quality if "diffCoverage" in q]
    hunks = (last or {}).get("hunks") or []
    left_out = (last or {}).get("hunksLeftOut") or []
    elapsed = (last or {}).get("elapsedSeconds")
    return {
        "mutationReports": len(reports),
        "mutationSkipped": (last or {}).get("skipped"),
        "mutationMode": (last or {}).get("mode"),
        "mutationSeconds": elapsed,
        "mutationHunks": len(hunks),
        "mutationHunksExercised": sum(1 for x in hunks if x.get("exercised")),
        "mutationHunksLeftOut": len(left_out),
        "timeCapReached": bool(left_out)
        and isinstance(elapsed, (int, float))
        and elapsed >= ((last or {}).get("maxSeconds") or MAX_SECONDS),
        "hunkCapReached": bool(left_out)
        and isinstance(elapsed, (int, float))
        and elapsed < ((last or {}).get("maxSeconds") or MAX_SECONDS),
        "testFileClasses": [
            f"{x.get('path')}:{x.get('class')}" for x in (last or {}).get("testFiles") or []
        ],
        "diffCoverage": coverage[-1] if coverage else None,
        "fastLaneSkipped": sorted(set(_skipped_steps(state_database(h, ws), run_id))),
        "runSeconds": round(sum(s.get("seconds", 0) for s in h.steps), 3),
    }


SUITE = interception.Suite(
    suite_id="H35",
    title="light mutation off/warn/enforce",
    build_baseline=build_baseline,
    cases=CASES,
    conditions=CONDITIONS,
    task_for=task_for,
    detect=detect,
    rules=FP_RULES,
    relevant=("tests.",),
    after_decision=after,
)


def runner(
    item: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    record = interception.run_one(SUITE, item, condition, args, rep)
    if "apparatusError" in record:
        raise RuntimeError(record["apparatusError"])
    record["style"] = item["style"]
    record["function"] = item["function"]
    rules = {f["rule"] for f in record.get("findings") or []}
    record["notExercisedFinding"] = "tests.change-not-exercised" in rules
    record["weakFinding"] = "tests.weak" in rules
    record["mutationFinding"] = bool(rules & set(FP_RULES))
    record["brief"] = (
        f"detected={record.get('detected')} seconds={record.get('mutationSeconds')} "
        f"skipped={record.get('mutationSkipped') or record.get('fastLaneSkipped')}"
    )
    return record


# ----- summary ---------------------------------------------------------------------------------
def stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    by_condition: dict[str, Any] = {}
    for condition in CONDITIONS:
        rows = [r for r in ok if r["condition"] == condition]
        if not rows:
            continue
        weak = [r for r in rows if r["kind"] == "seeded"]
        strong = [r for r in rows if r["kind"] == "clean"]
        seconds = [
            float(r["mutationSeconds"])
            for r in rows
            if isinstance(r.get("mutationSeconds"), (int, float))
        ]
        by_condition[condition] = {
            "weakDetected": proportion(sum(r["detected"] for r in weak), len(weak)),
            "weakNotExercisedFinding": proportion(
                sum(r["notExercisedFinding"] for r in weak), len(weak)
            ),
            "weakTestsWeakFinding": proportion(sum(r["weakFinding"] for r in weak), len(weak)),
            "weakBlocked": proportion(sum(r["blocked"] for r in weak), len(weak)),
            "byStyle": {
                style: proportion(
                    sum(r["detected"] for r in weak if r["style"] == style),
                    sum(1 for r in weak if r["style"] == style),
                )
                for style in STYLES[:3]
            },
            "missed": sorted(r["case"] for r in weak if not r["detected"]),
            "controlFalsePositive": proportion(
                sum(r["mutationFinding"] for r in strong), len(strong)
            ),
            "controlBlockedAnyCause": proportion(sum(r["blocked"] for r in strong), len(strong)),
            "controlFalsePositiveCases": sorted(r["case"] for r in strong if r["mutationFinding"]),
            "runsWithMutationReport": proportion(
                sum(1 for r in rows if r.get("mutationReports")), len(rows)
            ),
            "mutationNotRun": {
                r["case"]: r.get("mutationSkipped")
                or (
                    "fast lane skipped mutation"
                    if "mutation" in r.get("fastLaneSkipped", [])
                    else "no mutation report"
                )
                for r in rows
                if r.get("mutationSkipped") or not r.get("mutationReports")
            },
            "mutationSeconds": stats(seconds),
            "runSeconds": stats([float(r.get("runSeconds") or 0) for r in rows]),
            "timeCapReached": sum(1 for r in rows if r.get("timeCapReached")),
            "hunkCapReached": sum(1 for r in rows if r.get("hunkCapReached")),
            "diffCoverage": stats(
                [
                    float(r["diffCoverage"])
                    for r in rows
                    if isinstance(r.get("diffCoverage"), (int, float))
                ]
            ),
            "fastLaneSkippedMutation": sum(
                1 for r in rows if "mutation" in (r.get("fastLaneSkipped") or [])
            ),
            "rulesObserved": interception.rule_counts(rows),
        }
    warn = (by_condition.get("mutation-warn") or {}).get("mutationSeconds") or {}
    median = warn.get("median")
    summary: dict[str, Any] = {
        "suite": "H35",
        "title": "light mutation off/warn/enforce",
        "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION (a "
        "declared simulation); nobody decides at DECISION",
        "operators": "one: revert each non-cosmetic changed hunk and run the relevant tests; "
        "plus the class of each new test file on the baseline sources "
        "(orchestration/ladder_mutation.py, ladder/hunks.py)",
        "cases": {
            "weak": sum(c["kind"] == "seeded" for c in CASES),
            "strong": sum(c["kind"] == "clean" for c in CASES),
        },
        "byCondition": by_condition,
    }
    summary["criteria"] = {
        "statement": "detection and seconds are reported (no threshold in favour); if the "
        "median mutation seconds of mutation-warn (all changes S) exceed 60 s the mutation is "
        "marked a candidate to turn off in the fast lane (already in friction.fastLane.skip)",
        "mutationWarnMedianSeconds": median,
        "fastLaneOffCandidate": (median > FAST_LANE_CANDIDATE_SECONDS)
        if isinstance(median, (int, float))
        else None,
        "evaluable": isinstance(median, (int, float)),
        "detection": {c: (by_condition.get(c) or {}).get("weakDetected") for c in MEASURED},
        "controlFalsePositive": {
            c: (by_condition.get(c) or {}).get("controlFalsePositive") for c in MEASURED
        },
    }
    return summary


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H35",
            title="light mutation off/warn/enforce",
            stem="h35-light-mutation",
            cases=CASES,
            conditions_of=lambda item: list(CONDITIONS),
            all_conditions=list(CONDITIONS),
            runner=runner,
            summarize=summarize,
        )
    )
