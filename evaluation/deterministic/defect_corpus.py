#!/usr/bin/env python3
"""H11: the integrated defect-escape corpus (``CATALOGO.md`` §2.1, ``PREREGISTRO.md``). No model.

An aggregator: it reads the per-case records other suites already wrote and never re-runs,
filters or drops a case. Each ``--inputs`` directory is the output directory of one suite; the
reader is chosen from the records it finds there:

* the interception engine (``interception.py``: H01, H03, H05 and any later suite built on it,
  such as H02, H04 or H09): a record per case, condition and repetition with ``kind``
  (``seeded``, ``supplementary`` and ``escape`` are defective, ``clean`` is correct), ``group``,
  ``delivered`` (D*) or ``blocked`` (governed);
* N01-a (``ladder-corpus.jsonl``): ``truth.defective`` from the hidden oracle, under the
  configuration ``harness init`` writes (condition ``harness``);
* N02-a (``review-corpus.jsonl``): seeded or clean review branches, the silent review (reviewers
  that call no model and answer PASS: condition ``harness-no-model-panel``);
* any suite whose records already carry the fields of this corpus: ``suite``, ``case``,
  ``condition``, ``defective`` (bool) and ``delivered`` (bool), optionally ``rep``.

Every record may override the derived fields with ``defectClass``, ``language``, ``source``
(``own`` or ``external``) and ``deterministicRule`` (bool), so a new suite plugs in without a
change here.

Definitions (fixed by this script, declared in the summary):

* *delivered* under D*: the record's ``delivered`` (the visible tests pass). Under a governed
  condition: not ``blocked`` (the run stays in DECISION with the gate ``PASSED``,
  ``PREREGISTRO.md``); N01-a the same from its phase and gate; N02-a: the silent review's verdict
  is neither ``FAIL`` nor ``UNKNOWN`` (as ``review_corpus.py`` counts its false positives);
* *escape rate* = defective delivered / defective; *false-alarm rate* = correct not delivered /
  correct; 95 % Wilson intervals;
* repetitions: a defective case counts as escaped if it was delivered in any repetition, a correct
  case as a false alarm if it was blocked in any (the worst case for the harness); the rows whose
  repetitions differ are listed;
* a record whose suite apparatus failed (``apparatusError``) is *not evaluable* and is listed;
  a (case, condition, repetition) that ``environment.json`` announces and no record carries is
  *missing* and is listed; neither enters a denominator, both are reported.

The pre-registered criterion is evaluated as it is: "H reduces the escape at least to half of
D*'s in the classes covered by deterministic rules"; per covered class with defective cases under
both conditions: met when the escape rate of ``harness`` is at most half that of
``direct-equivalent``; not evaluable when a condition is absent or D* lets none escape.

Usage:
  python evaluation/deterministic/defect_corpus.py --out DIR --inputs DIR [DIR ...]
  python evaluation/deterministic/defect_corpus.py --self-check

Output: DIR/defect-corpus.jsonl (one row per suite, case and condition),
DIR/defect-corpus-summary.json, DIR/defect-corpus.md. Exit 0 when it aggregated every input;
1 when an input directory is absent, holds no records it can read, or holds unreadable records.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DIRECT = "direct-equivalent"
HARNESS = "harness"
DEFECTIVE_KINDS = {"seeded", "supplementary", "escape"}
CORRECT_KINDS = {"clean"}
OWN_SUITES = {"H01", "H03", "H05", "N01-a", "N02-a"}
H05_LANGUAGE = {"package.json": "javascript", "go.mod": "go", "Cargo.toml": "rust"}
EXTENSION_LANGUAGE = {
    ".py": "python",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".sh": "shell",
    ".md": "markdown",
    ".toml": "toml",
    ".json": "json",
}
# The suites the pre-registration names for H11 (H01-H10 plus N01-a and N02-a).
PREREGISTERED_SUITES = [f"H{n:02d}" for n in range(1, 11)] + ["N01-a", "N02-a"]
PREREGISTERED_REPS = 3
OUTPUT_NAMES = {"defect-corpus.jsonl"}


# ----- statistics -------------------------------------------------------------------------------
def wilson(successes: int, total: int, z: float = 1.959964) -> list[float] | None:
    """The 95 % Wilson score interval of a proportion (``CATALOGO.md`` §4.5)."""
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def proportion(successes: int, total: int) -> dict[str, Any]:
    return {
        "k": successes,
        "n": total,
        "rate": round(successes / total, 3) if total else None,
        "wilson95": wilson(successes, total),
    }


# ----- readers ----------------------------------------------------------------------------------
def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{number}: not a JSON object")
        rows.append(value)
    return rows


def language_of_path(path: str | None) -> str:
    return EXTENSION_LANGUAGE.get(Path(path or "").suffix, "unspecified") if path else "python"


def interception_trial(record: dict[str, Any]) -> dict[str, Any]:
    suite = str(record["suite"])
    kind = str(record.get("kind"))
    group = str(record.get("group"))
    defective = True if kind in DEFECTIVE_KINDS else False if kind in CORRECT_KINDS else None
    if suite == "H05":
        language = H05_LANGUAGE.get(group, "python")
    elif suite in OWN_SUITES:
        language = "python"
    else:
        language = "unspecified"
    if "apparatusError" in record:
        delivered = None
    elif record["condition"] == DIRECT:
        delivered = record.get("delivered")
    else:
        delivered = None if record.get("blocked") is None else not record["blocked"]
    return {
        "suite": suite,
        "case": str(record["case"]),
        "condition": str(record["condition"]),
        "rep": int(record.get("rep") or 1),
        "defective": defective,
        "kind": kind,
        "subset": record.get("set"),
        "defectClass": f"{suite}:{group}",
        "language": language,
        "source": "own" if suite in OWN_SUITES else "unspecified",
        "deterministicRule": defective is True and kind != "escape",
        "delivered": delivered,
        "apparatusError": "apparatusError" in record,
    }


def ladder_trial(record: dict[str, Any], condition: str) -> dict[str, Any]:
    truth = record.get("truth") or {}
    defective = truth.get("defective")
    at_decision = record.get("phase") == "DECISION" and record.get("gate") == "PASSED"
    known = record.get("phase") is not None or record.get("gate") is not None
    return {
        "suite": "N01-a",
        "case": str(record["case"]),
        "condition": condition,
        "rep": int(record.get("rep") or 1),
        "defective": None if defective is None else bool(defective),
        "kind": "seeded" if defective else "clean",
        "subset": f"part-{record.get('part')}",
        "defectClass": f"N01-a:part-{record.get('part')}",
        "language": "python",
        "source": "own",
        # Certification and light mutation are deterministic rules of the verification ladder.
        "deterministicRule": bool(defective),
        "delivered": at_decision if known else None,
        "apparatusError": not known,
    }


def review_trial(record: dict[str, Any], condition: str) -> dict[str, Any]:
    truth = record.get("truth") or []
    first = truth[0] if truth else {}
    verdict = (record.get("silent") or {}).get("verdict")
    rule = str(first.get("rule") or "")
    return {
        "suite": "N02-a",
        "case": str(record["case"]),
        "condition": condition,
        "rep": int(record.get("rep") or 1),
        "defective": bool(record.get("seeded")),
        "kind": "seeded" if record.get("seeded") else "clean",
        "subset": first.get("domain") or "clean",
        "defectClass": f"N02-a:{first.get('domain') or 'clean'}",
        "language": language_of_path(first.get("path")),
        "source": "own",
        # As review_corpus.py counts toolVerifiedRules: a rule a tool or a consistency check
        # verifies; the other domains need a reviewer.
        "deterministicRule": rule.startswith(("tool:", "consistency:")),
        "delivered": None if verdict is None else verdict not in {"FAIL", "UNKNOWN"},
        "apparatusError": verdict is None,
    }


def generic_trial(record: dict[str, Any]) -> dict[str, Any]:
    defective = bool(record["defective"])
    suite = str(record["suite"])
    return {
        "suite": suite,
        "case": str(record["case"]),
        "condition": str(record["condition"]),
        "rep": int(record.get("rep") or 1),
        "defective": defective,
        "kind": "seeded" if defective else "clean",
        "subset": record.get("set"),
        "defectClass": f"{suite}:{record.get('group') or ('defective' if defective else 'clean')}",
        "language": "unspecified",
        "source": "unspecified",
        "deterministicRule": defective,
        "delivered": None if "apparatusError" in record else record.get("delivered"),
        "apparatusError": "apparatusError" in record,
    }


OVERRIDES = ("defectClass", "language", "source", "deterministicRule")


def trials_of(path: Path, environment: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """The reader that fits the records of ``path`` and its trials (one per record)."""
    records = read_jsonl(path)
    if path.name == "ladder-corpus.jsonl":
        mode = environment.get("mutationMode") or "as written by harness init"
        condition = (
            HARNESS if mode == "as written by harness init" else f"{HARNESS}-mutation-{mode}"
        )
        reader, trials = "N01-a", [ladder_trial(r, condition) for r in records]
    elif path.name == "review-corpus.jsonl":
        reviewer = str(environment.get("reviewer") or "fixture (no model)")
        condition = "harness-no-model-panel" if reviewer.startswith("fixture") else "harness-panel"
        reader, trials = "N02-a", [review_trial(r, condition) for r in records]
    elif records and all({"suite", "case", "condition", "kind"} <= r.keys() for r in records):
        reader, trials = "interception", [interception_trial(r) for r in records]
    elif records and all(
        {"suite", "case", "condition", "defective", "delivered"} <= r.keys() for r in records
    ):
        reader, trials = "generic", [generic_trial(r) for r in records]
    else:
        raise ValueError(f"{path.name}: records of no known shape")
    for trial, record in zip(trials, records, strict=True):
        for key in OVERRIDES:
            if key in record:
                trial[key] = record[key]
    return reader, trials


def expected_grid(
    environment: dict[str, Any], trials: list[dict[str, Any]]
) -> set[tuple[str, str, str, int]]:
    """(suite, case, condition, rep) that ``environment.json`` announces."""
    cases = environment.get("cases")
    if not isinstance(cases, list) or not trials:
        return set()
    suites = {t["suite"] for t in trials}
    conditions = environment.get("conditions")
    if not isinstance(conditions, list):
        conditions = sorted({t["condition"] for t in trials})
    reps = int(environment.get("reps") or 1)
    return {
        (suite, str(case), str(condition), rep)
        for suite in suites
        for case in cases
        for condition in conditions
        for rep in range(1, reps + 1)
    }


def load_inputs(directories: Iterable[Path]) -> dict[str, Any]:
    trials: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    errors: list[str] = []
    missing: list[dict[str, Any]] = []
    for directory in directories:
        entry: dict[str, Any] = {"input": directory.name, "files": []}
        inputs.append(entry)
        if not directory.is_dir():
            errors.append(f"{directory.name}: input directory absent")
            entry["status"] = "absent"
            continue
        env_path = directory / "environment.json"
        environment: dict[str, Any] = {}
        if env_path.is_file():
            environment = json.loads(env_path.read_text(encoding="utf-8"))
        entry["environment"] = {
            key: environment.get(key)
            for key in ("suite", "harnessVersion", "wheelSha256", "repositoryHead", "reps")
        }
        files = sorted(p for p in directory.glob("*.jsonl") if p.name not in OUTPUT_NAMES)
        found: list[dict[str, Any]] = []
        for path in files:
            try:
                reader, items = trials_of(path, environment)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                errors.append(f"{directory.name}/{path.name}: {error}")
                continue
            entry["files"].append({"file": path.name, "reader": reader, "records": len(items)})
            found.extend(items)
        if not found:
            errors.append(f"{directory.name}: no records this aggregator can read")
            entry["status"] = "unreadable"
            continue
        entry["status"] = "read"
        if not environment:
            entry["note"] = "no environment.json: missing cases cannot be detected"
        seen = {(t["suite"], t["case"], t["condition"], t["rep"]) for t in found}
        for suite, case, condition, rep in sorted(expected_grid(environment, found) - seen):
            missing.append(
                {"input": directory.name, "suite": suite, "case": case,
                 "condition": condition, "rep": rep}
            )  # fmt: skip
        trials.extend(found)
    return {"trials": trials, "inputs": inputs, "errors": errors, "missing": missing}


# ----- the case table ---------------------------------------------------------------------------
def case_rows(trials: list[dict[str, Any]], missing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (suite, case, condition) with the outcome of every repetition."""
    truth: dict[tuple[str, str], dict[str, Any]] = {}
    for t in trials:
        if t["defective"] is not None:
            truth.setdefault((t["suite"], t["case"]), t)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for t in trials:
        grouped.setdefault((t["suite"], t["case"], t["condition"]), []).append(t)
    absent: dict[tuple[str, str, str], list[int]] = {}
    for m in missing:
        absent.setdefault((m["suite"], m["case"], m["condition"]), []).append(m["rep"])
    rows = []
    for key in sorted(set(grouped) | set(absent)):
        suite, case, condition = key
        items = sorted(grouped.get(key, []), key=lambda t: t["rep"])
        base = items[0] if items else truth.get((suite, case))
        outcomes = [t["delivered"] for t in items]
        known = [o for o in outcomes if o is not None]
        defective = base["defective"] if base else None
        if not known or defective is None:
            status = "missing" if not items else "not evaluable"
            delivered = None
        else:
            status = "evaluated"
            delivered = any(known) if defective else all(known)
        rows.append(
            {
                "suite": suite,
                "case": case,
                "condition": condition,
                "defective": defective,
                "kind": base["kind"] if base else None,
                "subset": base["subset"] if base else None,
                "defectClass": base["defectClass"] if base else None,
                "language": base["language"] if base else None,
                "source": base["source"] if base else None,
                "deterministicRule": base["deterministicRule"] if base else None,
                "repetitions": {str(t["rep"]): t["delivered"] for t in items},
                "repetitionsMissing": sorted(absent.get(key, [])),
                "apparatusErrors": sorted(t["rep"] for t in items if t["apparatusError"]),
                "identicalRepetitions": len(set(known)) <= 1,
                "status": status,
                "delivered": delivered,
                "escaped": bool(delivered) if status == "evaluated" and defective else None,
                "falseAlarm": (not delivered) if status == "evaluated" and not defective else None,
            }
        )
    return rows


def rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    defective = [r for r in rows if r["status"] == "evaluated" and r["defective"]]
    correct = [r for r in rows if r["status"] == "evaluated" and r["defective"] is False]
    return {
        "escape": proportion(sum(r["escaped"] for r in defective), len(defective)),
        "falseAlarm": proportion(sum(r["falseAlarm"] for r in correct), len(correct)),
    }


def breakdown(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return {
        str(value): rates([r for r in rows if r[key] == value])
        for value in sorted({r[key] for r in rows}, key=str)
    }


def criterion(rows: list[dict[str, Any]], input_errors: list[str]) -> dict[str, Any]:
    """The pre-registered criterion of H11, evaluated as it is; not evaluable as a whole when an
    input could not be read (the corpus is incomplete), with the status on the inputs read kept."""
    covered = sorted(
        {
            str(r["defectClass"])
            for r in rows
            if r["defective"] and r["deterministicRule"] and r["defectClass"]
        }
    )
    classes: dict[str, Any] = {}
    for name in covered:
        of = [r for r in rows if r["defectClass"] == name]
        direct = rates([r for r in of if r["condition"] == DIRECT])["escape"]
        harness = rates([r for r in of if r["condition"] == HARNESS])["escape"]
        if not direct["n"] or not harness["n"]:
            status, reason = "not evaluable", "no evaluated defective case under D* or H"
        elif direct["k"] == 0:
            status, reason = "not evaluable", "D* lets no defective case escape: nothing to halve"
        else:
            met = harness["k"] * direct["n"] * 2 <= direct["k"] * harness["n"]
            status, reason = ("met" if met else "not met"), None
        classes[name] = {"directEquivalent": direct, "harness": harness, "status": status}
        if reason:
            classes[name]["reason"] = reason
    evaluable = [c for c in classes.values() if c["status"] != "not evaluable"]
    if not evaluable:
        overall = "not evaluable"
    elif any(c["status"] == "not met" for c in evaluable):
        overall = "not met"
    else:
        overall = "met"
    result: dict[str, Any] = {
        "statement": "H reduces the escape at least to half of D*'s in the classes covered by deterministic rules; if not, the classes where it does not are listed",
        "status": overall,
        "classes": classes,
        "classesNotMet": sorted(n for n, c in classes.items() if c["status"] == "not met"),
        "classesNotEvaluable": sorted(
            n for n, c in classes.items() if c["status"] == "not evaluable"
        ),
        "classesNotCovered": sorted(
            {str(r["defectClass"]) for r in rows if r["defective"] and not r["deterministicRule"]}
        ),
    }
    if input_errors:
        result["statusOnInputsRead"] = overall
        result["status"] = "not evaluable"
        result["reason"] = "an input could not be read: " + "; ".join(input_errors)
    return result


def summarize(loaded: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_condition = {}
    for condition in sorted({r["condition"] for r in rows}):
        of = [r for r in rows if r["condition"] == condition]
        by_condition[condition] = {
            **rates(of),
            "cases": {
                "defective": sum(1 for r in of if r["defective"]),
                "correct": sum(1 for r in of if r["defective"] is False),
                "notEvaluable": sum(1 for r in of if r["status"] == "not evaluable"),
                "missing": sum(1 for r in of if r["status"] == "missing"),
            },
            "byClass": breakdown(of, "defectClass"),
            "byLanguage": breakdown(of, "language"),
            "bySource": breakdown(of, "source"),
            "bySuite": breakdown(of, "suite"),
            "escaped": sorted(f"{r['suite']}/{r['case']}" for r in of if r["escaped"]),
            "falseAlarms": sorted(f"{r['suite']}/{r['case']}" for r in of if r["falseAlarm"]),
        }
    suites = sorted({r["suite"] for r in rows})
    reps = {
        suite: max((len(r["repetitions"]) for r in rows if r["suite"] == suite), default=0)
        for suite in suites
    }
    deviations = []
    below = sorted(s for s, n in reps.items() if n < PREREGISTERED_REPS)
    if below:
        deviations.append(
            f"fewer than the {PREREGISTERED_REPS} pre-registered repetitions in: {', '.join(below)}"
        )
    absent = [s for s in PREREGISTERED_SUITES if s not in suites]
    if absent:
        deviations.append(f"pre-registered suites absent from this corpus: {', '.join(absent)}")
    if not any(r["source"] == "external" for r in rows):
        deviations.append(
            "no case from an external source (the catalogue's mitigation: half of the cases from public histories)"
        )
    return {
        "test": "H11 integrated defect-escape corpus (aggregator)",
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "modelCalls": 0,
        "inputs": loaded["inputs"],
        "inputErrors": loaded["errors"],
        "suites": suites,
        "repetitionsBySuite": reps,
        "rows": len(rows),
        "definitions": {
            "delivered": "D*: the visible tests pass; governed: the run stays in DECISION with the gate PASSED (not blocked); N02-a: the silent review's verdict is neither FAIL nor UNKNOWN",
            "escapeRate": "defective delivered / defective",
            "falseAlarmRate": "correct not delivered / correct",
            "repetitions": "a defective case escapes if delivered in any repetition; a correct case is a false alarm if blocked in any (worst case for the harness)",
            "notEvaluable": "the suite's apparatus failed on every repetition of the row; listed, never in a denominator",
            "missing": "announced by environment.json and absent from the records; listed, never in a denominator",
            "directEquivalent": "D* applies the seeded change and runs the visible tests; it is a model of a direct agent, not an agent (CATALOGO.md §4.4)",
            "simulatedPerson": "human.reviewer-simulated answers the waits before DECISION (a declared simulation)",
        },
        "byCondition": by_condition,
        "criterion": criterion(rows, loaded["errors"]),
        "notEvaluable": sorted(
            f"{r['suite']}/{r['case']}/{r['condition']}"
            for r in rows
            if r["status"] == "not evaluable"
        ),
        "missing": loaded["missing"],
        "nonIdenticalRepetitions": sorted(
            f"{r['suite']}/{r['case']}/{r['condition']}"
            for r in rows
            if not r["identicalRepetitions"]
        ),
        "deviations": deviations,
    }


# ----- output -----------------------------------------------------------------------------------
def cell(value: dict[str, Any]) -> str:
    if not value["n"]:
        return "—"
    low, high = value["wilson95"]
    return f"{value['k']}/{value['n']} ({value['rate']:.3f}; {low:.3f}–{high:.3f})"


def markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# H11 defect-escape corpus",
        "",
        "k/n (rate; 95 % Wilson interval). D* is a model of a direct agent, not an agent.",
        "",
        "| Condition | Escape (defective delivered) | False alarm (correct blocked) | Not evaluable | Missing |",
        "|---|---|---|---|---|",
    ]
    for name, row in summary["byCondition"].items():
        lines.append(
            f"| {name} | {cell(row['escape'])} | {cell(row['falseAlarm'])} | "
            f"{row['cases']['notEvaluable']} | {row['cases']['missing']} |"
        )
    crit = summary["criterion"]
    lines += [
        "",
        f"**Pre-registered criterion:** {crit['statement']}. **Status: {crit['status']}.**",
        *(
            ["", f"{crit['reason']} (status on the inputs read: {crit['statusOnInputsRead']})."]
            if "reason" in crit
            else []
        ),
        "",
        "| Class (deterministic rules) | Escape D* | Escape H | Status |",
        "|---|---|---|---|",
    ]
    for name, row in crit["classes"].items():
        lines.append(
            f"| {name} | {cell(row['directEquivalent'])} | {cell(row['harness'])} | {row['status']} |"
        )
    lines += ["", f"Classes not met: {', '.join(crit['classesNotMet']) or 'none'}."]
    lines.append(
        f"Classes not covered by deterministic rules: {', '.join(crit['classesNotCovered']) or 'none'}."
    )
    for title, key in (("Input errors", "inputErrors"), ("Deviations", "deviations")):
        if summary[key]:
            lines += ["", f"{title}:", *(f"- {item}" for item in summary[key])]
    if summary["missing"]:
        lines += ["", f"Missing (case, condition, repetition): {len(summary['missing'])}."]
    return "\n".join(lines) + "\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    print(f"wrote {path}", flush=True)


def aggregate(inputs: list[Path], out: Path) -> dict[str, Any]:
    loaded = load_inputs(inputs)
    rows = case_rows(loaded["trials"], loaded["missing"])
    summary = summarize(loaded, rows)
    write(out / "defect-corpus.jsonl", "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    write(out / "defect-corpus-summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    write(out / "defect-corpus.md", markdown(summary))
    return summary


# ----- self-check -------------------------------------------------------------------------------
def self_check() -> int:
    """Synthetic inputs with known numbers, one per reader; asserts the computation."""
    assert wilson(0, 10) == [0.0, 0.278], wilson(0, 10)
    assert wilson(5, 10) == [0.237, 0.763], wilson(5, 10)
    assert wilson(0, 0) is None
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def dump(
            directory: Path, name: str, records: list[dict[str, Any]], env: dict[str, Any]
        ) -> None:
            directory.mkdir(parents=True)
            write(directory / name, "".join(json.dumps(r) + "\n" for r in records))
            write(directory / "environment.json", json.dumps(env))

        conditions = [DIRECT, HARNESS]
        records = []
        # Class X: 4 seeded; D* delivers 4, H blocks 2, and x3 in rep 1 only (escape 2/4 <= half
        # of 4/4: met; x3 differs between repetitions).
        # Class Y: 2 seeded; D* delivers 2, H blocks 0 (2/2: not met). One clean case, blocked
        # by H (a false alarm). Case y2 under H has an apparatus error in rep 1 only. Case c2 is
        # announced by environment.json and absent from every record (missing).
        for case, group, kind in [("x1", "X", "seeded"), ("x2", "X", "seeded"), ("x3", "X", "seeded"),
                                  ("x4", "X", "seeded"), ("y1", "Y", "seeded"), ("y2", "Y", "seeded"),
                                  ("e1", "escape", "escape"), ("c1", "clean", "clean")]:  # fmt: skip
            for condition in conditions:
                for rep in (1, 2):
                    record = {"suite": "H01", "case": case, "condition": condition, "rep": rep,
                              "kind": kind, "group": group, "set": "inventory"}  # fmt: skip
                    if condition == DIRECT:
                        record["delivered"] = True
                    else:
                        record["blocked"] = case in {"x1", "x2", "c1"} or (
                            case == "x3" and rep == 1
                        )
                    if case == "y2" and condition == HARNESS and rep == 1:
                        record = {k: v for k, v in record.items() if k != "blocked"}
                        record["apparatusError"] = "boom"
                    records.append(record)
        cases = ["x1", "x2", "x3", "x4", "y1", "y2", "e1", "c1", "c2"]
        dump(
            root / "h01",
            "h01-weakened.jsonl",
            records,
            {"cases": cases, "conditions": conditions, "reps": 2},
        )
        ladder = [
            {"case": "l1", "rep": 1, "part": "c", "truth": {"defective": True}, "phase": "DECISION", "gate": "PASSED"},
            {"case": "l2", "rep": 1, "part": "c", "truth": {"defective": False}, "phase": "VERIFICATION", "gate": "FAILED"},
        ]  # fmt: skip
        dump(root / "ladder", "ladder-corpus.jsonl", ladder, {"cases": ["l1", "l2"], "reps": 1})
        review = [
            {"case": "r1", "seeded": True, "truth": [{"domain": "pipeline-security", "rule": "pipeline-security.weakened-gate", "path": ".github/workflows/ci.yml"}], "silent": {"verdict": "PASS"}},
            {"case": "r2", "seeded": False, "truth": [], "silent": {"verdict": "FAIL"}},
        ]  # fmt: skip
        dump(
            root / "review",
            "review-corpus.jsonl",
            review,
            {"cases": ["r1", "r2"], "reviewer": "fixture (no model)", "reps": 1},
        )
        generic = [{"suite": "H09", "case": "g1", "condition": HARNESS, "defective": True, "delivered": False,
                    "language": "go", "source": "external", "defectClass": "H09:cve"}]  # fmt: skip
        dump(root / "h09", "h09.jsonl", generic, {})
        summary = aggregate(
            [root / "h01", root / "ladder", root / "review", root / "h09", root / "absent"],
            root / "out",
        )
        by = summary["byCondition"]
        # D*: 7 defective H01 cases delivered (x1-x4, y1, y2, e1); correct c1 delivered.
        assert by[DIRECT]["escape"] == proportion(7, 7), by[DIRECT]["escape"]
        assert by[DIRECT]["falseAlarm"] == proportion(0, 1), by[DIRECT]["falseAlarm"]
        # H: H01 x3 (rep 2), x4, y1, y2 (rep 2), e1 escape; l1 escapes; g1 blocked: 6 of 9.
        assert by[HARNESS]["escape"] == proportion(6, 9), by[HARNESS]["escape"]
        # H: c1 and l2 blocked.
        assert by[HARNESS]["falseAlarm"] == proportion(2, 2), by[HARNESS]["falseAlarm"]
        assert by["harness-no-model-panel"]["escape"] == proportion(1, 1)
        assert by["harness-no-model-panel"]["falseAlarm"] == proportion(1, 1)
        assert by[HARNESS]["byLanguage"]["go"]["escape"] == proportion(0, 1)
        assert by[HARNESS]["bySource"]["external"]["escape"] == proportion(0, 1)
        crit = summary["criterion"]
        assert crit["classes"]["H01:X"]["status"] == "met", crit["classes"]["H01:X"]
        assert crit["classes"]["H01:Y"]["status"] == "not met", crit["classes"]["H01:Y"]
        assert crit["classes"]["N01-a:part-c"]["status"] == "not evaluable"
        assert crit["classesNotMet"] == ["H01:Y"], crit
        assert crit["status"] == "not evaluable", crit  # the absent input
        assert crit["statusOnInputsRead"] == "not met", crit
        assert "H01:escape" in crit["classesNotCovered"]
        assert summary["nonIdenticalRepetitions"] == ["H01/x3/harness"], summary
        assert summary["notEvaluable"] == [], summary["notEvaluable"]
        assert {(m["case"], m["condition"]) for m in summary["missing"]} == {
            ("c2", DIRECT),
            ("c2", HARNESS),
        }, summary["missing"]
        assert summary["inputErrors"] == ["absent: input directory absent"], summary["inputErrors"]
        rows = [
            json.loads(x) for x in (root / "out" / "defect-corpus.jsonl").read_text().splitlines()
        ]
        assert any(r["case"] == "c2" and r["status"] == "missing" for r in rows)
        assert len(rows) == 9 * 2 + 2 + 2 + 1, len(rows)
    print("self-check passed", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--inputs", type=Path, nargs="+", default=[])
    parser.add_argument("--self-check", action="store_true", help="run on synthetic input")
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()
    if args.out is None or not args.inputs:
        parser.error("--out and --inputs are required")
    summary = aggregate([p.resolve() for p in args.inputs], args.out.resolve())
    print(f"criterion: {summary['criterion']['status']}", flush=True)
    return 1 if summary["inputErrors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
