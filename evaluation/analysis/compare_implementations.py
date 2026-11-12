"""Compare HOW the implementations of the evaluation runs differ, beyond the hidden-test score.

Deterministic and offline: no model is called. Input: run directories as ``run_eval.py`` (and the
rides launcher) leave them (``ws/``, ``measure/`` or ``quarantine-ws/`` + ``measure-quarantine/``,
``agent-calls/``, the harness record under ``state/*/state.db`` or ``ws/.harness/state.db``). Per
run it computes (the metrics declared in the amendment of 2026-11-11 to
``evaluation/campaign/PREREGISTRO.md``):

* the final change against the baseline commit: files and lines, by kind (source, test, docs,
  other) and files outside the task's scope (``measure.SCOPE``);
* test functions added (``ast``), the outcome of the test cases of the changed test files (the
  junit of the visible suite) and the coverage of the changed source lines (``coverage.json``);
* static quality of the changed files with the pinned tools of the evaluation venv: Ruff (by rule,
  ``--isolated``), Mypy (the project's own configuration) and Bandit (by test id), each after minus
  before; secret patterns in the added lines;
* the public surface of the changed source modules (``ast``): names added, removed and with a
  changed signature, docstrings, changelog and documentation;
* requirements: the hidden tests by criterion (when the scenario maps them), and for the governed
  conditions what the harness recorded (questions, answers, assumptions, decisions, disputes,
  correction cycles, blocking validators); for the direct condition what its final text says
  (assumption and question lines), or ``available: false`` when the text is not stored.

Output: one JSON per run and ``comparison.md`` (in Spanish, as the rest of the evaluation) pairing
the direct run with each governed condition per (scenario, prompt, model, repetition), with a
"diferencias" section generated from the numbers only. Paths are depersonalized
(``agentlib.anonymize``).

Behaviour before what the prompt left unsaid (D8, poor and casual prompts of ``greenfield`` and
``security`` only; ``unknown_behavior``): ``preguntó`` > ``declaró supuestos`` > ``inventó``, first
rule that holds. (a) asked: the final text of a direct run (``resultText`` of ``agent-calls``; the
``.jsonl`` does not keep it) has a line that ends in ``?``, or the harness recorded clarification
questions. (b) declared assumptions: the final text, a comment or the README lines the run added say
explicitly that something is assumed (``asumo``, ``supuesto``, ``assume``, ``assumption``, a TODO to
confirm a value), or the harness recorded assumptions (self-report, ``metadata.assumptions`` of the
task). (c) invented: the new source code carries concrete values (numbers; word-like strings such as
zone or coupon names) that appear neither in ``SPEC.md`` of the scenario (numbers compared as
decimals, also as a percentage; strings case-insensitively) nor in an assumption the run declared.
Without any of the three the run is ``sin valores nuevos``. Literals 0, 1 and 2, docstrings, f-string
parts and strings with spaces (messages) are not values. It is a heuristic over literals, not a proof.

    python evaluation/analysis/compare_implementations.py --runs <work>/runs/pilot --out <dir>
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
sys.path.insert(0, str(EVALUATION))

import agentlib  # noqa: E402
import measure  # noqa: E402

sys.path.insert(0, str(HERE))
import practices  # noqa: E402

SCHEMA = "compare-implementations/1"
# Every ``harness-*`` name before ``harness``: the alternation of RUN_NAME takes the first that
# matches, so a shorter prefix first would read ``harness-nopanel`` as ``harness`` with
# the model ``nopanel-...``).
CONDITIONS = (
    "harness-anchored",
    "harness-nopanel",
    "harness-core",
    "harness-nopo",
    "harness",
    "direct-po",
    "direct",
    "baseline",
    "clarify",
)
RUN_NAME = re.compile(
    r"^(?P<scenario>[a-z]+)(?:-(?P<prompt>[a-z_]+))?-(?P<condition>"
    + "|".join(re.escape(c) for c in CONDITIONS)
    + r")-(?P<model>.+)-r(?P<rep>\d+)-(?P<stamp>\d{8}T\d{6}Z)$"
)
TASKS = {
    "brownfield": EVALUATION / "tasks" / "brownfield-itsdangerous.yaml",
    "greenfield": EVALUATION / "tasks" / "greenfield-shipping.yaml",
    "security": EVALUATION / "tasks" / "greenfield-alerts.yaml",
}
# Hidden test function -> criterion of the task file (None: the test checks something no criterion
# states explicitly). Fixed with the amendment, before the campaign results.
HIDDEN_CRITERIA: dict[str, dict[str, str | None]] = {
    "brownfield": {
        "test_valid_url_safe_input_still_decodes": "req_padding",
        "test_round_trip_for_every_length": "req_padding",
        "test_url_safe_alphabet_characters_are_accepted": "req_alphabet",
        "test_characters_outside_the_url_safe_alphabet_are_rejected": "ac_reject",
        "test_non_ascii_and_control_bytes_are_rejected": "ac_reject",
        "test_impossible_length_is_rejected": None,
        "test_signature_with_inserted_character_is_rejected": "ac_signatures",
        "test_serialized_token_with_inserted_character_is_rejected": "ac_signatures",
        "test_untampered_tokens_still_load": "ac_existing",
    },
}
DEDUCIBLE = "deducible"
SPEC_ONLY = "especificacion"
# D8: every hidden test function of the greenfield scenarios, marked as deducible without the
# specification (``SPEC.md`` / the criteria of the task file) or only with it. Rule, fixed before the
# campaign results: a function is ``deducible`` only when EVERY case it asserts follows from the
# signature, the types or general input validation (a negative amount is rejected, an unknown code is
# rejected, money comes back as ``Decimal`` with two places); one case that needs an exact value, a
# name, a limit, a default or a rule that only the specification states makes it ``especificacion``.
# The inputs of a ``deducible`` test still use names that only the specification gives (zones,
# coupon codes), so the count is an upper bound of what a casual prompt can reach.
HIDDEN_DEDUCIBILITY: dict[str, dict[str, str]] = {
    "greenfield": {
        # exact tariffs by weight band and zone: only in the specification
        "test_base_cost_by_weight": SPEC_ONLY,
        # the 30 kg limit and the exclusive lower bound are stated values, not general validation
        "test_weight_out_of_range_is_rejected": SPEC_ONLY,
        # a negative amount is invalid in any money API: general validation
        "test_negative_subtotal_is_rejected": DEDUCIBLE,
        # asserts the exact price 5.00 of the base tariff
        "test_zero_subtotal_is_accepted": SPEC_ONLY,
        # "Local" and "LOCAL" are rejected: case sensitivity is a stated rule
        "test_unknown_zone_is_rejected": SPEC_ONLY,
        # the 50 % surcharge is a stated value
        "test_express_adds_half_of_base": SPEC_ONLY,
        # which zone cannot be express is a stated rule
        "test_express_international_is_rejected": SPEC_ONLY,
        # the 100 threshold and its inclusiveness are stated values
        "test_free_shipping_threshold_is_inclusive": SPEC_ONLY,
        "test_free_shipping_excludes_express_and_international": SPEC_ONLY,
        # coupon values, rounding rule and normalisation are stated
        "test_percentage_coupon_rounds_half_up_once": SPEC_ONLY,
        "test_coupon_codes_ignore_case_and_whitespace": SPEC_ONLY,
        "test_flat_coupon_never_goes_below_zero": SPEC_ONLY,
        "test_coupon_on_free_shipping_stays_zero": SPEC_ONLY,
        # an unknown code is invalid input whatever the catalogue: general validation
        "test_unknown_coupon_is_rejected": DEDUCIBLE,
        # asserts the price 5.00 with no coupon
        "test_absent_coupon_means_no_discount": SPEC_ONLY,
        # exact results for float, string and Decimal inputs
        "test_numeric_inputs_of_every_type": SPEC_ONLY,
        # asserts only the type and the number of decimals: money as Decimal with two places
        "test_result_is_decimal_with_two_places": DEDUCIBLE,
    },
    "security": {
        # method, exact URL and header values are stated
        "test_request_method_url_and_headers": SPEC_ONLY,
        # the ``base_url`` parameter and its normalisation are stated
        "test_trailing_slash_in_base_url_is_ignored": SPEC_ONLY,
        # key order, separators, encoding and trimming of the body are stated
        "test_body_is_sorted_compact_utf8_json": SPEC_ONLY,
        # the default ``app`` is a stated value
        "test_source_defaults_to_app": SPEC_ONLY,
        # the user name, the variable name and the Basic scheme are stated
        "test_basic_auth_uses_the_environment_password": SPEC_ONLY,
        "test_password_is_read_at_call_time": SPEC_ONLY,
        # the variable name and ``RuntimeError`` are stated
        "test_missing_password_raises_runtime_error": SPEC_ONLY,
        # the allowed severities (``debug`` and ``INFO`` rejected) are stated values
        "test_invalid_events_are_rejected": SPEC_ONLY,
    },
}
RIDES_CASES = EVALUATION / "rides" / "hidden_cases.json"
SECRET_PATTERNS = {
    "declaredLiteral": re.compile(re.escape(measure.SECRET_LITERAL)),
    "awsAccessKey": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "privateKey": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "assignedCredential": re.compile(
        r"(?i)\b(?:password|passwd|pwd|api_?key|secret_?key|access_?token|auth_?token)\b"
        r"\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"
    ),
}
ASSUMPTION_LINE = re.compile(
    r"(?i)\b(assum\w*|presum\w*|suppos\w*|interpret\w*|I chose|I decided|asum\w*|supus\w*|supuest\w*)"
)
DOC_NAMES = ("readme", "changes", "changelog", "history", "news")
MAX_TEXTS = 12
MAX_CHARS = 300
EXCERPT_LINES = 60
PROMPT_ORDER = {"poor": 0, "one_line": 0, "casual": 1, "full": 2}


# ----------------------------------------------------------------------------- run discovery


def parse_run_name(name: str) -> dict[str, Any] | None:
    """Scenario, prompt (``full`` when the name has no tag), condition, model, repetition."""
    match = RUN_NAME.match(name)
    if not match:
        return None
    meta = match.groupdict()
    condition = "direct" if meta["condition"] == "baseline" else meta["condition"]
    return {
        "scenario": meta["scenario"],
        "prompt": meta["prompt"] or "full",
        "condition": condition,
        "model": meta["model"],
        "rep": int(meta["rep"]),
        "stamp": meta["stamp"],
    }


def run_metadata(run_dir: Path) -> dict[str, Any] | None:
    meta = parse_run_name(run_dir.name)
    record = run_dir / "record.json"  # the rides launcher keeps its record next to the workspace
    if record.is_file():
        data = json.loads(record.read_text(encoding="utf-8"))
        meta = {
            **(meta or {}),
            "scenario": "rides",
            "prompt": str(data.get("level") or (meta or {}).get("prompt") or "unknown"),
            "condition": str(data.get("condition") or (meta or {}).get("condition") or "unknown"),
            "model": str(data.get("model") or (meta or {}).get("model") or "unknown"),
            "rep": int(data.get("rep") or (meta or {}).get("rep") or 1),
        }
    return meta


def discover(paths: Iterable[Path]) -> tuple[list[Path], list[str]]:
    """Run directories among ``paths`` (each a run directory or a directory of them), and the
    names of the directories skipped because they are not a recognizable run."""
    found: list[Path] = []
    skipped: list[str] = []
    for path in paths:
        candidates = (
            [path] if (path / "ws").is_dir() else sorted(p for p in path.iterdir() if p.is_dir())
        )
        for candidate in candidates:
            if (candidate / "ws").is_dir() and run_metadata(candidate):
                found.append(candidate)
            else:
                skipped.append(candidate.name)
    return found, skipped


# ----------------------------------------------------------------------------- helpers


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **measure.GIT_ENV},
    )


def baseline_commit(workspace: Path) -> str | None:
    roots = _git(workspace, "rev-list", "--max-parents=0", "HEAD").stdout.split()
    return roots[-1] if roots else None


def worktree_diff(workspace: Path, baseline: str, *diff_args: str) -> str:
    """``git diff --cached`` of the whole working tree (untracked files included) against the
    baseline, through a temporary index so the workspace's own index is left untouched."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, **measure.GIT_ENV, "GIT_INDEX_FILE": str(Path(tmp) / "index")}

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", *args],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        run("read-tree", baseline)
        run("add", "-A")
        return run("diff", "--cached", *diff_args).stdout


def _relative(item: dict[str, Any], root: Path) -> str:
    return Path(item["filename"]).resolve().relative_to(root.resolve()).as_posix()


def baseline_text(workspace: Path, baseline: str, path: str) -> str | None:
    shown = _git(workspace, "show", f"{baseline}:{path}")
    return shown.stdout if shown.returncode == 0 else None


def file_kind(path: str) -> str:
    name = Path(path).name.lower()
    parts = Path(path).parts
    if parts[0] in ("tests", "test") or name.startswith("test_") or name.endswith("_test.py"):
        return "test"
    if parts[0] == "src":
        return "source"
    if parts[0] == "docs" or name.startswith(DOC_NAMES) or name.endswith((".md", ".rst")):
        return "docs"
    return "other"


def _junit_cases(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    cases = []
    for case in ET.parse(path).getroot().iter("testcase"):
        outcome = "passed"
        for child in case:
            if child.tag in ("failure", "error", "skipped"):
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[child.tag]
        cases.append(
            {
                "classname": case.get("classname", ""),
                "name": case.get("name", ""),
                "outcome": outcome,
            }
        )
    return cases


def _tally(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(c["outcome"] for c in cases)
    return {
        "tests": len(cases),
        "passed": counts["passed"],
        "failed": counts["failed"],
        "errors": counts["error"],
        "skipped": counts["skipped"],
    }


def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…"


def _tool_version(module: str) -> str | None:
    proc = subprocess.run(
        [sys.executable, "-m", module, "--version"], capture_output=True, text=True
    )
    out = (proc.stdout or proc.stderr).strip().splitlines()
    return out[0] if proc.returncode == 0 and out else None


# ----------------------------------------------------------------------------- the change


def change_metrics(files: list[dict[str, Any]], scenario: str) -> dict[str, Any]:
    scope = measure.SCOPE.get(scenario)
    by_kind: dict[str, dict[str, int]] = {}
    for item in files:
        cell = by_kind.setdefault(file_kind(item["path"]), {"files": 0, "added": 0, "removed": 0})
        cell["files"] += 1
        cell["added"] += item["added"]
        cell["removed"] += item["removed"]
    out_of_scope = (
        sorted(f["path"] for f in files if not f["path"].startswith(scope)) if scope else None
    )
    return {
        "files": len(files),
        "addedLines": sum(f["added"] for f in files),
        "removedLines": sum(f["removed"] for f in files),
        "byKind": dict(sorted(by_kind.items())),
        "outOfScopePaths": out_of_scope,
        "outOfScopeFiles": len(out_of_scope) if out_of_scope is not None else None,
        "paths": sorted(f["path"] for f in files),
    }


def count_test_functions(text: str | None) -> int:
    if not text:
        return 0
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0
    count = 0
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            count += 1
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            count += sum(
                1
                for item in node.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                and item.name.startswith("test")
            )
    return count


def _module_of(path: str) -> str:
    return path[:-3].replace("/", ".") if path.endswith(".py") else path


def test_metrics(
    workspace: Path, baseline: str, files: list[dict[str, Any]], visible_junit: Path
) -> dict[str, Any]:
    tests = [f for f in files if file_kind(f["path"]) == "test" and f["path"].endswith(".py")]
    per_file = []
    cases = _junit_cases(visible_junit)
    for item in tests:
        path = workspace / item["path"]
        after = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
        before = baseline_text(workspace, baseline, item["path"])
        module = _module_of(item["path"])
        mine = [
            c for c in cases if c["classname"] == module or c["classname"].startswith(module + ".")
        ]
        per_file.append(
            {
                "path": item["path"],
                "new": before is None,
                "functionsBefore": count_test_functions(before),
                "functionsAfter": count_test_functions(after),
                "cases": _tally(mine),
            }
        )
    cases_total = _tally(
        [
            c
            for item in tests
            for c in cases
            if c["classname"] == _module_of(item["path"])
            or c["classname"].startswith(_module_of(item["path"]) + ".")
        ]
    )
    return {
        "changedTestFiles": len(tests),
        "newTestFiles": sum(1 for f in per_file if f["new"]),
        "testFunctionsAdded": sum(f["functionsAfter"] - f["functionsBefore"] for f in per_file),
        "casesInChangedFiles": cases_total if visible_junit.is_file() else None,
        "visibleJunitAvailable": visible_junit.is_file(),
        "perFile": per_file,
    }


def coverage_metrics(files: list[dict[str, Any]], coverage_json: Path) -> dict[str, Any]:
    if not coverage_json.is_file():
        return {"available": False, "reason": "coverage.json not stored for this tree"}
    measured = json.loads(coverage_json.read_text(encoding="utf-8")).get("files", {})
    measured = {name.replace("\\", "/"): entry for name, entry in measured.items()}
    executable = covered = 0
    for item in files:
        if file_kind(item["path"]) != "source" or not item["path"].endswith(".py"):
            continue
        entry = measured.get(item["path"])
        if not entry:
            continue
        run = set(entry["executed_lines"])
        changed = (run | set(entry["missing_lines"])) & set(item["addedLines"])
        executable += len(changed)
        covered += len(changed & run)
    return {
        "available": True,
        "executableChanged": executable,
        "coveredChanged": covered,
        "ratio": round(covered / executable, 4) if executable else None,
    }


# ----------------------------------------------------------------------------- static quality


def _ruff(paths: list[Path], cwd: Path) -> list[dict[str, Any]] | None:
    if not paths:
        return []
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--output-format",
            "json",
            *map(str, paths),
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    try:
        return list(json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError:
        return None


def _bandit(paths: list[Path], cwd: Path) -> list[dict[str, Any]] | None:
    if not paths:
        return []
    proc = subprocess.run(
        [sys.executable, "-m", "bandit", "-q", "-f", "json", *map(str, paths)],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    try:
        return list(json.loads(proc.stdout or "{}").get("results", []))
    except json.JSONDecodeError:
        return None


MYPY_LINE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): error: (?P<message>.*?)(?:\s+\[(?P<code>[a-z0-9-]+)\])?$"
)


def _mypy(tree: Path, targets: list[str], cache: Path) -> list[tuple[str, str, str]] | None:
    """Mypy errors of ``tree`` as (path, code, message) with the project's own configuration."""
    config = tree / "pyproject.toml"
    args = [
        sys.executable,
        "-m",
        "mypy",
        "--python-version",
        f"{sys.version_info.major}.{sys.version_info.minor}",
        "--no-pretty",
        "--no-error-summary",
        "--show-error-codes",
        "--no-color-output",
        "--cache-dir",
        str(cache),
    ]
    if config.is_file():
        args += ["--config-file", str(config)]
    proc = subprocess.run(
        [*args, *targets],
        cwd=tree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode not in (0, 1):
        return None
    errors = []
    for line in proc.stdout.splitlines():
        match = MYPY_LINE.match(line.strip())
        if match:
            errors.append(
                (
                    match["path"].replace("\\", "/"),
                    match["code"] or "unknown",
                    re.sub(r"\s+", " ", match["message"]),
                )
            )
    return errors


def _mypy_targets(tree: Path, changed_sources: list[str]) -> list[str]:
    config = tree / "pyproject.toml"
    if config.is_file():
        section = tomllib.loads(config.read_text(encoding="utf-8")).get("tool", {}).get("mypy", {})
        if section.get("files"):
            return []  # the configuration names the files
    return [p for p in changed_sources if (tree / p).is_file()]


def _new(after: Counter[Any], before: Counter[Any]) -> dict[str, int]:
    return {str(k): v for k, v in sorted((after - before).items())}


def static_metrics(workspace: Path, baseline: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    python = [
        f["path"] for f in files if f["path"].endswith(".py") and (workspace / f["path"]).is_file()
    ]
    sources = [p for p in python if file_kind(p) == "source"]
    with tempfile.TemporaryDirectory(prefix="cmp-") as tmp:
        scratch = Path(tmp)
        after_dir, before_dir = scratch / "after", scratch / "before"
        after, before = [], []
        for name in python:
            target = after_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(workspace / name, target)
            after.append(target)
            old = baseline_text(workspace, baseline, name)
            if old is not None:
                previous = before_dir / name
                previous.parent.mkdir(parents=True, exist_ok=True)
                previous.write_text(old, encoding="utf-8")
                before.append(previous)
        ruff_after, ruff_before = _ruff(after, scratch), _ruff(before, scratch)
        ruff: dict[str, Any] = {"available": ruff_after is not None and ruff_before is not None}
        if ruff["available"]:
            assert ruff_after is not None and ruff_before is not None
            by_kind_after = Counter(file_kind(_relative(i, after_dir)) for i in ruff_after)
            by_kind_before = Counter(file_kind(_relative(i, before_dir)) for i in ruff_before)
            rules_after = Counter(str(i.get("code")) for i in ruff_after)
            rules_before = Counter(str(i.get("code")) for i in ruff_before)
            new_rules = _new(rules_after, rules_before)
            ruff.update(
                {
                    "after": len(ruff_after),
                    "before": len(ruff_before),
                    "new": sum(new_rules.values()),
                    "newByRule": new_rules,
                    "newByKind": _new(by_kind_after, by_kind_before),
                }
            )
        src_after = [p for p in after if file_kind(p.relative_to(after_dir).as_posix()) == "source"]
        src_before = [
            p for p in before if file_kind(p.relative_to(before_dir).as_posix()) == "source"
        ]
        bandit_after, bandit_before = _bandit(src_after, scratch), _bandit(src_before, scratch)
        bandit: dict[str, Any] = {
            "available": bandit_after is not None and bandit_before is not None
        }
        if bandit["available"]:
            assert bandit_after is not None and bandit_before is not None
            new_tests = _new(
                Counter(f"{i['test_id']}:{i['issue_severity']}" for i in bandit_after),
                Counter(f"{i['test_id']}:{i['issue_severity']}" for i in bandit_before),
            )
            bandit.update(
                {
                    "after": len(bandit_after),
                    "before": len(bandit_before),
                    "new": sum(new_tests.values()),
                    "newByTest": new_tests,
                }
            )
        mypy: dict[str, Any] = {"available": False}
        if sources or (workspace / "pyproject.toml").is_file():
            base_tree = scratch / "base"
            base_tree.mkdir()
            archive = scratch / "base.tar"
            _git(workspace, "archive", "--format=tar", "-o", str(archive), baseline)
            if archive.is_file() and archive.stat().st_size:
                shutil.unpack_archive(archive, base_tree, filter="data")
                targets = _mypy_targets(workspace, sources)
                if targets or (workspace / "pyproject.toml").is_file():
                    errors_after = _mypy(workspace, targets, scratch / "cache-after")
                    errors_before = _mypy(
                        base_tree,
                        [t for t in targets if (base_tree / t).is_file()],
                        scratch / "cache-before",
                    )
                    if errors_after is not None and errors_before is not None:
                        new_errors = Counter(errors_after) - Counter(errors_before)
                        mypy = {
                            "available": True,
                            "after": len(errors_after),
                            "before": len(errors_before),
                            "new": sum(new_errors.values()),
                            "newByCode": dict(
                                sorted(
                                    Counter(code for _, code, _ in new_errors.elements()).items()
                                )
                            ),
                            "newErrors": [
                                {"path": p, "code": c, "message": _short(m)}
                                for (p, c, m) in sorted(new_errors.elements())[:MAX_TEXTS]
                            ],
                        }
    return {"ruff": ruff, "mypy": mypy, "bandit": bandit}


def secret_metrics(workspace: Path, baseline: str) -> dict[str, Any]:
    """Secret patterns in the lines the change added (so only what is new against the baseline)."""
    patch = worktree_diff(workspace, baseline, "-U0", baseline)
    found: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    files: set[str] = set()
    current = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif (
            line.startswith("+")
            and current
            and not set(Path(current).parts) & measure.EXCLUDED_PARTS
        ):
            for name, pattern in SECRET_PATTERNS.items():
                if pattern.search(line[1:]):
                    found[name] += 1
                    by_kind[file_kind(current)] += 1
                    files.add(current)
    return {
        "newMatches": sum(found.values()),
        "newMatchesByKind": dict(sorted(by_kind.items())),
        "byPattern": dict(sorted(found.items())),
        "files": sorted(files),
    }


# ----------------------------------------------------------------------------- public surface


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({ast.unparse(node.args)}){returns}"


def public_surface(text: str | None) -> dict[str, dict[str, Any]]:
    """Public names of a module: name -> {kind, signature, docstring}."""
    if not text:
        return {}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    names: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith(
            "_"
        ):
            names[node.name] = {
                "kind": "function",
                "signature": _signature(node),
                "doc": ast.get_docstring(node),
            }
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            names[node.name] = {
                "kind": "class",
                "signature": f"({bases})",
                "doc": ast.get_docstring(node),
            }
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and (
                    not item.name.startswith("_") or item.name == "__init__"
                ):
                    names[f"{node.name}.{item.name}"] = {
                        "kind": "method",
                        "signature": _signature(item),
                        "doc": ast.get_docstring(item),
                    }
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names[target.id] = {"kind": "variable", "signature": "", "doc": None}
                elif (
                    isinstance(target, ast.Name)
                    and target.id == "__all__"
                    and node.value is not None
                ):
                    names["__all__"] = {
                        "kind": "all",
                        "signature": ast.unparse(node.value),
                        "doc": None,
                    }
    return names


def surface_metrics(workspace: Path, baseline: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []
    docstrings_added = docstrings_changed = documented = public = 0
    for item in files:
        if file_kind(item["path"]) != "source" or not item["path"].endswith(".py"):
            continue
        path = workspace / item["path"]
        after = public_surface(
            path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
        )
        before = public_surface(baseline_text(workspace, baseline, item["path"]))
        module = _module_of(item["path"])
        added += [f"{module}:{n}" for n in sorted(after.keys() - before.keys())]
        removed += [f"{module}:{n}" for n in sorted(before.keys() - after.keys())]
        changed += [
            f"{module}:{n}"
            for n in sorted(after.keys() & before.keys())
            if after[n]["signature"] != before[n]["signature"]
        ]
        for name, entry in after.items():
            if entry["kind"] in ("variable", "all"):
                continue
            public += 1
            documented += bool(entry["doc"])
            old = before.get(name)
            if entry["doc"] and (old is None or not old["doc"]):
                docstrings_added += 1
            elif old and entry["doc"] and old["doc"] and entry["doc"] != old["doc"]:
                docstrings_changed += 1
    docs = [f for f in files if file_kind(f["path"]) == "docs"]
    return {
        "publicNamesAdded": added,
        "publicNamesRemoved": removed,
        "signaturesChanged": changed,
        "publicDefinitionsInChangedModules": public,
        "documentedDefinitions": documented,
        "docstringsAdded": docstrings_added,
        "docstringsChanged": docstrings_changed,
        "changelogUpdated": any(
            Path(f["path"]).name.lower().startswith(("changes", "changelog", "history", "news"))
            for f in docs
        ),
        "docFilesChanged": sorted(f["path"] for f in docs),
    }


# ----------------------------------------------------------------------------- requirements


def criteria_map(scenario: str) -> Callable[[dict[str, Any]], str | None] | None:
    if scenario in HIDDEN_CRITERIA:
        table = HIDDEN_CRITERIA[scenario]
        return lambda case: table.get(case["name"].split("[", 1)[0], None)
    if scenario == "rides" and RIDES_CASES.is_file():
        listing = json.loads(RIDES_CASES.read_text(encoding="utf-8"))
        return lambda case: listing.get(case["classname"].rsplit(".", 1)[-1] + "::" + case["name"])
    return None


def deducibility(scenario: str, cases: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Hidden tests passed and total of the greenfield scenarios by ``HIDDEN_DEDUCIBILITY``: the two
    counts that tell what a prompt without the specification could reach. ``None`` for scenarios
    without the table; a test missing from it counts under ``unclassified``."""
    table = HIDDEN_DEDUCIBILITY.get(scenario)
    if table is None:
        return None
    out: dict[str, Any] = {
        DEDUCIBLE: {"passed": 0, "total": 0},
        SPEC_ONLY: {"passed": 0, "total": 0},
        "unclassified": 0,
    }
    for case in cases:
        kind = table.get(case["name"].split("[", 1)[0])
        if kind is None:
            out["unclassified"] += 1
            continue
        out[kind]["total"] += 1
        out[kind]["passed"] += case["outcome"] == "passed"
    return out


def hidden_metrics(scenario: str, junit: Path) -> dict[str, Any]:
    cases = _junit_cases(junit)
    if not cases:
        return {"available": False}
    mapper = criteria_map(scenario)
    by_criterion: dict[str, list[int]] = {}
    for case in cases:
        criterion = (mapper(case) if mapper else None) or "(sin criterio)"
        tally = by_criterion.setdefault(criterion, [0, 0])
        tally[0] += case["outcome"] == "passed"
        tally[1] += 1
    mapped = {k: v for k, v in by_criterion.items() if k != "(sin criterio)"}
    tally = _tally(cases)
    return {
        "available": True,
        **tally,
        # The fixed denominator and the discriminating tests (amendment of 2026-11-12).
        **measure.hidden_score(scenario, tally, junit),
        "mapping": mapper is not None,
        "deducibility": deducibility(scenario, cases),
        "byCriterion": {
            k: {"passed": v[0], "tests": v[1]} for k, v in sorted(by_criterion.items())
        },
        "criteriaFullyPassed": sum(1 for v in mapped.values() if v[0] == v[1]) if mapper else None,
        "criteriaMapped": len(mapped) if mapper else None,
        "failing": sorted(c["name"] for c in cases if c["outcome"] != "passed")[:MAX_TEXTS],
    }


def prompt_metrics(run_dir: Path, meta: dict[str, Any]) -> dict[str, Any]:
    """What the prompt gave the agent: the criteria of the task file, or none for a casual text."""
    if meta["prompt"] == "casual":
        casual = TASKS.get(meta["scenario"])
        text = None
        if casual and casual.with_name(casual.stem + "-casual.txt").is_file():
            text = casual.with_name(casual.stem + "-casual.txt").read_text(encoding="utf-8").strip()
        return {"kind": "casual", "criteriaGiven": 0, "requirementsGiven": 0, "text": text}
    task = TASKS.get(meta["scenario"])
    if meta["prompt"] == "poor" and task:
        task = task.with_name(task.stem + "-poor.yaml")
    if not task or not task.is_file():
        return {"kind": meta["prompt"], "criteriaGiven": None, "requirementsGiven": None}
    import yaml

    data = yaml.safe_load(task.read_text(encoding="utf-8")) or {}
    return {
        "kind": meta["prompt"],
        "criteriaGiven": len(data.get("acceptanceCriteria") or []),
        "requirementsGiven": len(data.get("requirements") or []),
        "criterionIds": [c.get("criterionId") for c in data.get("acceptanceCriteria") or []],
    }


def state_path(run_dir: Path, workspace: Path) -> Path | None:
    for root in (run_dir / "ws", workspace):
        local = root / ".harness" / "state.db"
        if local.is_file():
            return local
    found = sorted((run_dir / "state").glob("*/state.db")) if (run_dir / "state").is_dir() else []
    return found[0] if found else None


def harness_metrics(db_path: Path, visible_junit: Path) -> dict[str, Any]:
    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    stored: dict[str, list[dict[str, Any]]] = {}
    try:
        for kind, payload in db.execute(
            "select record_type, payload_json from records where record_type != 'artifact'"
            " order by rowid"
        ):
            stored.setdefault(kind, []).append(json.loads(payload))
        events = [
            (t, json.loads(p))
            for t, p in db.execute(
                "select event_type, payload_json from events order by global_sequence"
            )
        ]
    finally:
        db.close()

    def records(kind: str) -> list[dict[str, Any]]:
        return stored.get(kind, [])

    task = (records("task") or [{}])[-1]
    execution = (records("execution") or [{}])[-1]
    requests = records("clarification_request")
    clarifications = records("clarification")
    reports = records("agent_self_report")
    findings = records("finding")
    validations = records("validation")
    questions = [q for r in requests for q in r.get("questions") or []]
    answers = [a for c in clarifications for a in c.get("answers") or []]
    assumptions = [a for r in reports for a in r.get("assumptions") or []]
    disputed = [d for r in reports for d in r.get("disputedAcceptanceTests") or []]
    decided = [t for t, _ in events if t.endswith(".decided") and not t.startswith("agent.routing")]
    corrections = [p for t, p in events if t == "correction.authorized"]
    exhausted = [p for t, p in events if t == "correction.exhausted"]
    blocking = sorted(
        {
            str(v.get("validatorId"))
            for v in validations
            if v.get("mandatory") and v.get("status") == "FAILED"
        }
        | {str(v) for p in corrections + exhausted for v in p.get("failedValidators") or []}
    )
    frozen = next(
        (
            sorted((p.get("frozen") or {}).keys())
            for t, p in reversed(events)
            if t == "acceptance.tests.decided"
        ),
        [],
    )
    criteria = [c.get("criterionId") for c in task.get("acceptanceCriteria") or []]
    cases = _junit_cases(visible_junit)
    acceptance = []
    for path in frozen:
        module = _module_of(path)
        mine = [
            c for c in cases if c["classname"] == module or c["classname"].startswith(module + ".")
        ]
        acceptance.append({"path": path, "cases": _tally(mine)})
    covered = sorted(c for c in criteria if c and any(c in Path(p).stem for p in frozen))
    severities = Counter(str(f.get("severity")) for f in findings)
    rules = Counter(str(f.get("ruleId")) for f in findings)
    return {
        "finalPhase": execution.get("currentPhase"),
        "finalStatus": execution.get("status"),
        "terminalReason": _short(execution.get("terminalReason") or "") or None,
        "taskFormalized": {
            "criteria": len(criteria),
            "requirements": len(task.get("requirements") or []),
            "constraints": len(task.get("constraints") or []),
        },
        "clarification": {
            "rounds": len(requests),
            "questions": len(questions),
            "answers": len(answers),
            "criteriaAddedByAnswers": sum(
                len(c.get("addedCriteria") or []) for c in clarifications
            ),
            "requirementsAddedByAnswers": sum(
                len(c.get("addedRequirements") or []) for c in clarifications
            ),
            "questionTexts": [_short(a.get("question") or "") for a in answers[:MAX_TEXTS]],
        },
        "assumptions": {
            "taskMetadata": len((task.get("metadata") or {}).get("assumptions") or []),
            "count": len(assumptions),
            "texts": [_short(a) for a in assumptions[:MAX_TEXTS]],
        },
        "decisions": {
            "events": dict(sorted(Counter(decided).items())),
            "gate": [str(d.get("decision")) for d in records("decision")],
        },
        "disputes": {
            "disputedTests": len(disputed),
            "events": sum(1 for t, _ in events if "dispute" in t),
        },
        "corrections": {"cycles": len(corrections), "exhausted": bool(exhausted)},
        "blockingValidators": blocking,
        "findings": {
            "total": len(findings),
            "bySeverity": dict(sorted(severities.items())),
            "topRules": dict(rules.most_common(10)),
            "unsupportedClaims": rules["agent.unsupported-claim"],
        },
        "acceptanceTests": {
            "frozenFiles": len(frozen),
            "criteriaWithAcceptanceFile": len(covered),
            "criteria": len(criteria),
            "cases": _tally(
                [
                    c
                    for path in frozen
                    for c in cases
                    if c["classname"] == _module_of(path)
                    or c["classname"].startswith(_module_of(path) + ".")
                ]
            )
            if cases
            else None,
            "perFile": acceptance,
        },
    }


def agent_metrics(run_dir: Path) -> dict[str, Any]:
    calls = agentlib.load_calls(run_dir / "agent-calls")
    texts = [str(c.get("resultText") or "") for c in calls if c.get("resultText")]
    return {
        "calls": len(calls),
        "turns": sum(int(c.get("numTurns") or 0) for c in calls),
        "costUsd": round(sum(float(c.get("costUsd") or 0) for c in calls), 4),
        "permissionDenials": sum(int(c.get("permissionDenials") or 0) for c in calls),
        "productOwnerCalls": len(agentlib.load_calls(run_dir / "po-calls")),
        "resultTextStored": bool(texts),
    }


def direct_text_metrics(run_dir: Path) -> dict[str, Any]:
    """What the direct agent's final text says it assumed or asked (it cannot wait for an answer)."""
    calls = agentlib.load_calls(run_dir / "agent-calls")
    texts = [str(c.get("resultText") or "") for c in calls if c.get("resultText")]
    if not texts:
        return {"available": False, "reason": "the agent's final text is not stored"}
    lines = [line.strip() for text in texts for line in text.splitlines() if line.strip()]
    assumption = [line for line in lines if ASSUMPTION_LINE.search(line)]
    questions = [line for line in lines if line.rstrip("*_` ").endswith("?")]
    return {
        "available": True,
        "source": "resultText of agent-calls (final message only; the transcript is not stored)",
        "chars": sum(len(t) for t in texts),
        "assumptionLines": len(assumption),
        "questionLines": len(questions),
        "assumptionTexts": [_short(x) for x in assumption[:MAX_TEXTS]],
        "questionTexts": [_short(x) for x in questions[:MAX_TEXTS]],
    }


# ----------------------------------------------------------------------------- unknowns (D8)

UNKNOWN_SCENARIOS = ("greenfield", "security")
UNKNOWN_PROMPTS = ("poor", "one_line", "casual")
ASKED, ASSUMED, INVENTED, NOTHING_NEW = (
    "preguntó",
    "declaró supuestos",
    "inventó",
    "sin valores nuevos",
)
EXPLICIT_ASSUMPTION = re.compile(
    r"(?i)\b(asumo|asumi\w*|supuest\w*|assum(?:e|es|ed|ing|ption|ptions))\b"
    r"|\bTODO\b.*\b(confirm\w*|verific\w*|validar|to confirm)\b"
)
TRIVIAL_NUMBERS = {Decimal(0), Decimal(1), Decimal(2)}
WORD_LIKE = re.compile(r"^[^\s%{}]{2,24}$")


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def spec_values(scenario: str) -> tuple[set[Decimal], set[str]] | None:
    """Numbers (also as ``n / 100``, for percentages) and lower-case words of ``SPEC.md`` of the
    scenario; ``None`` when the file is not there."""
    task = TASKS.get(scenario)
    spec = EVALUATION / "fixtures" / task.stem / "SPEC.md" if task else None
    if spec is None or not spec.is_file():
        return None
    text = spec.read_text(encoding="utf-8")
    numbers: set[Decimal] = set()
    for raw in re.findall(r"\d+(?:\.\d+)?", text):
        number = Decimal(raw)
        numbers |= {number, number / 100}
    return numbers, {w.lower() for w in re.findall(r"[^\W\d][\w-]*", text)}


def code_literals(source: str) -> Counter[tuple[str, str]]:
    """Concrete values in the code: ``("n", normalized number)`` and ``("s", word-like string)``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return Counter()
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            skip |= {id(c) for c in ast.walk(node) if isinstance(c, ast.Constant)}
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
    found: Counter[tuple[str, str]] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in skip or isinstance(node.value, bool):
            continue
        value = node.value
        number = None
        if isinstance(value, int | float):
            number = _decimal(value)
        elif isinstance(value, str) and WORD_LIKE.match(value):
            number = _decimal(value)  # ``Decimal("5.00")`` carries a number as text
            if number is None:
                found[("s", value.lower())] += 1
                continue
        if number is not None and abs(number) not in TRIVIAL_NUMBERS:
            found[("n", format(abs(number).normalize(), "f"))] += 1
    return found


def _added(workspace: Path, baseline: str, path: str) -> list[str]:
    target = workspace / path
    if not target.is_file():
        return []
    old = Counter((baseline_text(workspace, baseline, path) or "").splitlines())
    added = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        if old[line] > 0:
            old[line] -= 1
        else:
            added.append(line)
    return added


def unknown_behavior(
    scenario: str,
    workspace: Path,
    baseline: str,
    files: list[dict[str, Any]],
    asked: int,
    declared: int,
    final_text: str = "",
) -> dict[str, Any]:
    """What the run did before what the prompt left unsaid (see the module docstring). ``asked`` and
    ``declared`` count what the condition recorded (question lines or clarification questions;
    assumptions recorded); the explicit statements in the final text and in the lines the run added
    are counted here."""
    declared_lines = [x for x in final_text.splitlines() if EXPLICIT_ASSUMPTION.search(x)]
    new_literals: Counter[tuple[str, str]] = Counter()
    for item in files:
        path = item["path"]
        if file_kind(path) == "source" and path.endswith(".py"):
            target = workspace / path
            if target.is_file():
                after = code_literals(target.read_text(encoding="utf-8", errors="replace"))
                before = code_literals(baseline_text(workspace, baseline, path) or "")
                new_literals += after - before
        if file_kind(path) in ("source", "docs") or Path(path).name.lower().startswith("readme"):
            declared_lines += [
                x for x in _added(workspace, baseline, path) if EXPLICIT_ASSUMPTION.search(x)
            ]
    known = spec_values(scenario)
    declared_text = " ".join(declared_lines).lower()
    invented = []
    if known is not None:
        numbers, words = known
        for kind, value in sorted(new_literals):
            if kind == "n" and Decimal(value) in numbers:
                continue
            if kind == "s" and value in words:
                continue
            if value in declared_text:
                continue
            invented.append(value)
    declared_total = declared + len(declared_lines)
    if asked > 0:
        behavior = ASKED
    elif declared_total > 0:
        behavior = ASSUMED
    elif invented:
        behavior = INVENTED
    else:
        behavior = NOTHING_NEW
    return {
        "behavior": behavior,
        "questions": asked,
        "assumptionsDeclared": declared_total,
        "inventedValues": len(invented),
        "inventedTexts": invented[:MAX_TEXTS],
        "specAvailable": known is not None,
    }


# ----------------------------------------------------------------------------- practices and code


def practice_metrics(workspace: Path, baseline: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    """Good and bad practices (``practices.py``) of the changed Python files, after and before."""
    out: dict[str, Any] = {}
    for kind, analyze in (
        ("source", practices.source_practices),
        ("tests", practices.test_practices),
    ):
        wanted = "source" if kind == "source" else "test"
        after: dict[str, str] = {}
        before: dict[str, str] = {}
        for item in files:
            if file_kind(item["path"]) != wanted or not item["path"].endswith(".py"):
                continue
            path = workspace / item["path"]
            if path.is_file():
                after[item["path"]] = path.read_text(encoding="utf-8", errors="replace")
            old = baseline_text(workspace, baseline, item["path"])
            if old is not None:
                before[item["path"]] = old
        measured_after, measured_before = analyze(after), analyze(before)
        out[kind] = {
            "files": len(after),
            "after": measured_after,
            "before": measured_before,
            "delta": practices.delta(measured_after, measured_before),
        }
    return out


def code_excerpt(workspace: Path, baseline: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    """The diff of the changed source files (context 1), cut at ``EXCERPT_LINES`` lines."""
    sources = [f["path"] for f in files if file_kind(f["path"]) == "source"]
    if not sources:
        return {"lines": 0, "truncated": False, "text": ""}
    diff = worktree_diff(workspace, baseline, "-U1", "--no-color", baseline, "--", *sources)
    lines = [line for line in diff.splitlines() if not line.startswith(("index ", "diff --git"))]
    return {
        "lines": len(lines),
        "truncated": len(lines) > EXCERPT_LINES,
        "text": "\n".join(lines[:EXCERPT_LINES]),
    }


# ----------------------------------------------------------------------------- one run


def _pytest(tree: Path, targets: list[str], junit: Path, coverage: Path | None = None) -> int:
    env = {**os.environ, "PYTHONPATH": str(tree / "src")}
    runner = [sys.executable, "-m", "pytest"]
    if coverage is not None:
        env["COVERAGE_FILE"] = str(coverage / ".coverage")
        runner = [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            "--source=src",
            "-m",
            "pytest",
        ]
    argv = [*runner, "-q", "-p", "no:cacheprovider", "--color=no", f"--junitxml={junit}", *targets]
    try:
        code = subprocess.run(argv, cwd=tree, env=env, capture_output=True, timeout=900).returncode
    except subprocess.TimeoutExpired:
        code = -1
    if coverage is not None:
        subprocess.run(
            [sys.executable, "-m", "coverage", "json", "-q", "-o", str(coverage / "coverage.json")],
            cwd=tree,
            env=env,
            capture_output=True,
        )
    return code


def reconstruct_quarantine(
    run_dir: Path, baseline: str, scenario: str, tmp: Path
) -> tuple[Path, Path, dict[str, Any]]:
    """The stopped run's own change: the baseline tree with ``quarantined.patch`` applied.

    The apparatus copy (``quarantine-ws``) is not used when the patch did not apply to it: the
    kept acceptance tests are already in the restored workspace ("already exists") and the patch
    saved on Windows has CRLF line ends ("patch does not apply"), so that copy holds the baseline
    source. Here the patch is applied, with LF line ends, to a clean export of the baseline; the
    hidden tests, the visible suite and the coverage are run again on that tree (no model call).
    Returns the tree, the directory of its junit and coverage files, and what was done."""
    tree, scratch = tmp / "tree", tmp / "measure"
    tree.mkdir()
    scratch.mkdir()
    archive = tmp / "base.tar"
    _git(run_dir / "ws", "archive", "--format=tar", "-o", str(archive), baseline)
    shutil.unpack_archive(archive, tree, filter="data")
    identity = [
        "-c",
        "user.name=eval",
        "-c",
        "user.email=eval@invalid",
        "-c",
        "commit.gpgsign=false",
    ]
    for args in (["init", "-q"], ["add", "-A"], [*identity, "commit", "-qm", "baseline"]):
        _git(tree, *args)
    patch = tmp / "quarantined.patch"
    raw = (run_dir / "quarantined.patch").read_bytes()
    patch.write_bytes(raw.replace(b"\r\n", b"\n"))
    applied = _git(tree, "apply", "--whitespace=nowarn", str(patch))
    info: dict[str, Any] = {
        "patchApplied": applied.returncode == 0,
        "patchHadCrlf": b"\r\n" in raw,
        "applyStderr": _short(applied.stderr) or None,
        "baseline": _git(tree, "rev-parse", "HEAD").stdout.strip(),
    }
    if applied.returncode != 0:
        return tree, scratch, info
    hidden_source = EVALUATION / measure.HIDDEN[scenario] if scenario in measure.HIDDEN else None
    if hidden_source and hidden_source.is_file():
        target = tree / "_hidden_eval"
        target.mkdir()
        shutil.copy2(hidden_source, target)
        _pytest(tree, [str(target)], scratch / "hidden.xml")
        shutil.rmtree(target)
    info["visibleExit"] = _pytest(tree, [], scratch / "visible.xml", coverage=scratch)
    for leftover in (".coverage",):
        (tree / leftover).unlink(missing_ok=True)
    return tree, scratch, info


def analyze_run(run_dir: Path, static: bool = True) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="cmp-run-") as tmp:
        return _analyze_run(run_dir, static, Path(tmp))


def _analyze_run(run_dir: Path, static: bool, tmp: Path) -> dict[str, Any]:
    meta = run_metadata(run_dir)
    if meta is None:
        raise ValueError(f"not a run directory: {run_dir.name}")
    outcome = run_outcome(run_dir)
    if outcome.get("refused") or outcome.get("invalid") == "apparatus-run-start-failed":
        # Nothing ran: the tree is the baseline, so no metric of it is the run's (2026-11-12).
        status = "rechazada" if outcome.get("refused") else "no iniciada (aparato)"
        return {
            "schema": SCHEMA,
            "run": {
                **meta,
                "id": run_id(meta),
                "measuredOn": None,
                "status": status,
                "outcome": outcome.get("outcome"),
            },
            "notMeasured": True,
        }
    baseline = baseline_commit(run_dir / "ws")
    if baseline is None:
        raise ValueError(f"no baseline commit in {run_dir.name}")
    original = baseline
    workspace, scratch, measured_on = run_dir / "ws", run_dir / "measure", "workspace"
    reconstruction = None
    if (run_dir / "quarantined.patch").is_file():
        tree, rebuilt, reconstruction = reconstruct_quarantine(
            run_dir, baseline, meta["scenario"], tmp
        )
        if reconstruction["patchApplied"]:
            workspace, scratch, measured_on = tree, rebuilt, "quarantine-reconstructed"
            baseline = reconstruction.pop("baseline")
        elif (run_dir / "quarantine-ws").is_dir():
            workspace, scratch, measured_on = (
                run_dir / "quarantine-ws",
                run_dir / "measure-quarantine",
                "quarantine-copy",
            )
    files = measure._changed_files(workspace, baseline)
    visible = scratch / "visible.xml"
    hidden_junit = (
        scratch / "hidden.xml" if (scratch / "hidden.xml").is_file() else scratch / "oracle.xml"
    )
    out: dict[str, Any] = {
        "schema": SCHEMA,
        "run": {**meta, "id": run_id(meta), "measuredOn": measured_on},
        "change": change_metrics(files, meta["scenario"]),
        "tests": test_metrics(workspace, baseline, files, visible),
        "coverage": coverage_metrics(files, scratch / "coverage.json"),
        "static": static_metrics(workspace, baseline, files) if static else {"skipped": True},
        "secrets": secret_metrics(workspace, baseline),
        "surface": surface_metrics(workspace, baseline, files),
        "practices": practice_metrics(workspace, baseline, files),
        "code": code_excerpt(workspace, baseline, files),
        "requirements": {
            "prompt": prompt_metrics(run_dir, meta),
            "hidden": hidden_metrics(meta["scenario"], hidden_junit),
        },
        "agent": agent_metrics(run_dir),
    }
    if reconstruction is not None:
        apparatus = run_dir / "measure-quarantine" / "hidden.xml"
        copy = run_dir / "quarantine-ws"
        reconstruction.pop("baseline", None)
        reconstruction["apparatusCopy"] = {
            "hidden": _tally(_junit_cases(apparatus)) if apparatus.is_file() else None,
            "changedPaths": sorted(f["path"] for f in measure._changed_files(copy, original))
            if copy.is_dir()
            else None,
        }
        final = run_dir / "measure-final-tree" / "hidden.xml"
        reconstruction["finalTreeHidden"] = _tally(_junit_cases(final)) if final.is_file() else None
        out["quarantine"] = reconstruction
    if meta["condition"] in ("direct", "direct-po"):
        out["requirements"]["direct"] = direct_text_metrics(run_dir)
    else:
        db = state_path(run_dir, run_dir / "ws")
        out["requirements"]["harness"] = (
            harness_metrics(db, visible)
            if db
            else {"available": False, "reason": "no state database"}
        )
    if meta["scenario"] in UNKNOWN_SCENARIOS and meta["prompt"] in UNKNOWN_PROMPTS:
        requirements = out["requirements"]
        direct_side = requirements.get("direct")
        harness_side = requirements.get("harness") or {}
        if direct_side is not None:
            asked = int(direct_side.get("questionLines") or 0)
            declared = 0
            text = "\n".join(
                str(c.get("resultText") or "") for c in agentlib.load_calls(run_dir / "agent-calls")
            )
        else:
            asked = int((harness_side.get("clarification") or {}).get("questions") or 0)
            recorded = harness_side.get("assumptions") or {}
            declared = int(recorded.get("count") or 0) + int(recorded.get("taskMetadata") or 0)
            text = ""
        requirements["unknowns"] = unknown_behavior(
            meta["scenario"], workspace, baseline, files, asked, declared, text
        )
    return out


def run_outcome(run_dir: Path) -> dict[str, Any]:
    """``run-outcome.json`` written by ``run_eval.py`` (outcome, refused, invalid); empty for a
    run directory written before 2026-11-12."""
    path = run_dir / "run-outcome.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def run_id(meta: dict[str, Any]) -> str:
    return f"{meta['scenario']}-{meta['prompt']}-{meta['condition']}-{meta['model']}-r{meta['rep']}"


# ----------------------------------------------------------------------------- comparison table


def _secrets_outside_tests(record: dict[str, Any]) -> Any:
    kinds = _get(record, "secrets.newMatchesByKind")
    if kinds is None:
        return None
    return sum(n for kind, n in kinds.items() if kind != "test")


# (label, getter, polarity): +1 more is better, -1 less is better, 0 no direction is claimed.
Row = tuple[str, Callable[[dict[str, Any]], Any], int]
ROWS: list[Row] = [
    ("Estado de la corrida", lambda r: _get(r, "run.status") or "medida", 0),
    ("Pruebas ocultas pasadas", lambda r: _get(r, "requirements.hidden.passed"), 1),
    ("Pruebas ocultas (total recolectado)", lambda r: _get(r, "requirements.hidden.tests"), 0),
    ("Pruebas ocultas (nominal)", lambda r: _get(r, "requirements.hidden.nominal"), 0),
    (
        "Pruebas ocultas discriminantes pasadas",
        lambda r: _get(r, "requirements.hidden.discriminating.passed"),
        1,
    ),
    (
        "Pruebas ocultas discriminantes (total)",
        lambda r: _get(r, "requirements.hidden.discriminating.total"),
        0,
    ),
    (
        "Criterios con todas sus pruebas ocultas en verde",
        lambda r: _get(r, "requirements.hidden.criteriaFullyPassed"),
        1,
    ),
    (
        "Pruebas ocultas deducibles sin especificación pasadas",
        lambda r: _get(r, "requirements.hidden.deducibility.deducible.passed"),
        0,
    ),
    (
        "Pruebas ocultas deducibles sin especificación (total)",
        lambda r: _get(r, "requirements.hidden.deducibility.deducible.total"),
        0,
    ),
    (
        "Pruebas ocultas solo con especificación pasadas",
        lambda r: _get(r, "requirements.hidden.deducibility.especificacion.passed"),
        0,
    ),
    (
        "Pruebas ocultas solo con especificación (total)",
        lambda r: _get(r, "requirements.hidden.deducibility.especificacion.total"),
        0,
    ),
    ("Criterios dados en el prompt", lambda r: _get(r, "requirements.prompt.criteriaGiven"), 0),
    ("Archivos cambiados", lambda r: _get(r, "change.files"), 0),
    ("Archivos fuera del alcance", lambda r: _get(r, "change.outOfScopeFiles"), -1),
    ("Líneas añadidas", lambda r: _get(r, "change.addedLines"), 0),
    ("Líneas eliminadas", lambda r: _get(r, "change.removedLines"), 0),
    ("Archivos fuente cambiados", lambda r: _get(r, "change.byKind.source.files"), 0),
    ("Líneas añadidas en fuente", lambda r: _get(r, "change.byKind.source.added"), 0),
    ("Archivos de prueba cambiados", lambda r: _get(r, "change.byKind.test.files"), 0),
    ("Líneas añadidas en pruebas", lambda r: _get(r, "change.byKind.test.added"), 0),
    (
        "Archivos de otro tipo (scripts sueltos, etc.)",
        lambda r: _get(r, "change.byKind.other.files"),
        -1,
    ),
    ("Funciones de prueba nuevas (netas)", lambda r: _get(r, "tests.testFunctionsAdded"), 1),
    (
        "Casos en archivos de prueba cambiados",
        lambda r: _get(r, "tests.casesInChangedFiles.tests"),
        0,
    ),
    (
        "Casos que fallan en esos archivos",
        lambda r: _sum(r, "tests.casesInChangedFiles.failed", "tests.casesInChangedFiles.errors"),
        -1,
    ),
    ("Cobertura de líneas fuente cambiadas", lambda r: _get(r, "coverage.ratio"), 1),
    ("Ruff: hallazgos nuevos", lambda r: _get(r, "static.ruff.new"), -1),
    ("Ruff: hallazgos nuevos en fuente", lambda r: _get(r, "static.ruff.newByKind.source"), -1),
    (
        "Ruff: imports o variables sin usar nuevos (F401, F841)",
        lambda r: (
            _sum(r, "static.ruff.newByRule.F401", "static.ruff.newByRule.F841") or 0
            if _get(r, "static.ruff.available")
            else None
        ),
        -1,
    ),
    ("Mypy: errores nuevos", lambda r: _get(r, "static.mypy.new"), -1),
    ("Bandit: hallazgos nuevos (fuente)", lambda r: _get(r, "static.bandit.new"), -1),
    ("Secretos nuevos en código no de prueba (patrones)", _secrets_outside_tests, -1),
    (
        "Literales de credencial nuevos en pruebas (fixtures)",
        lambda r: _get(r, "secrets.newMatchesByKind.test") or 0 if _get(r, "secrets") else None,
        0,
    ),
    ("Nombres públicos añadidos", lambda r: _len(r, "surface.publicNamesAdded"), 0),
    ("Nombres públicos eliminados", lambda r: _len(r, "surface.publicNamesRemoved"), -1),
    ("Firmas públicas cambiadas", lambda r: _len(r, "surface.signaturesChanged"), -1),
    ("Docstrings añadidas", lambda r: _get(r, "surface.docstringsAdded"), 1),
    ("Docstrings modificadas", lambda r: _get(r, "surface.docstringsChanged"), 0),
    ("Changelog actualizado", lambda r: _get(r, "surface.changelogUpdated"), 1),
    (
        "Preguntas de aclaración (INTENT)",
        lambda r: _get(r, "requirements.harness.clarification.questions"),
        0,
    ),
    ("Respuestas recibidas", lambda r: _get(r, "requirements.harness.clarification.answers"), 0),
    (
        "Criterios formalizados en la tarea",
        lambda r: _get(r, "requirements.harness.taskFormalized.criteria"),
        0,
    ),
    (
        "Requisitos formalizados en la tarea",
        lambda r: _get(r, "requirements.harness.taskFormalized.requirements"),
        0,
    ),
    (
        "Criterios con archivo de aceptación",
        lambda r: _get(r, "requirements.harness.acceptanceTests.criteriaWithAcceptanceFile"),
        0,
    ),
    (
        "Supuestos registrados (harness) o líneas de supuesto (direct)",
        lambda r: _first(
            r, "requirements.harness.assumptions.count", "requirements.direct.assumptionLines"
        ),
        0,
    ),
    (
        "Preguntas en el texto final (direct)",
        lambda r: _get(r, "requirements.direct.questionLines"),
        0,
    ),
    (
        "Decisiones humanas registradas",
        lambda r: _dict_sum(r, "requirements.harness.decisions.events"),
        0,
    ),
    (
        "Pruebas de aceptación disputadas",
        lambda r: _get(r, "requirements.harness.disputes.disputedTests"),
        0,
    ),
    ("Ciclos de corrección", lambda r: _get(r, "requirements.harness.corrections.cycles"), 0),
    ("Validadores que bloquearon", lambda r: _len(r, "requirements.harness.blockingValidators"), 0),
    (
        "Afirmaciones no sustentadas del agente",
        lambda r: _get(r, "requirements.harness.findings.unsupportedClaims"),
        -1,
    ),
    ("Fase final", lambda r: _get(r, "requirements.harness.finalPhase"), 0),
    ("Estado final", lambda r: _get(r, "requirements.harness.finalStatus"), 0),
    ("Llamadas al agente", lambda r: _get(r, "agent.calls"), 0),
    ("Turnos del agente", lambda r: _get(r, "agent.turns"), 0),
    ("Costo del agente (USD)", lambda r: _get(r, "agent.costUsd"), -1),
]


def _practice(kind: str, key: str, value: str = "delta") -> Callable[[dict[str, Any]], Any]:
    return lambda r: (
        _get(r, f"practices.{kind}.{value}.{key}") if _get(r, f"practices.{kind}.files") else None
    )


# Counts are what the change added (after minus before: delta); ratios and maxima are of the final
# code of the changed files (after).
PRACTICE_ROWS: list[Row] = [
    ("Fuente: funciones (nuevas, netas)", _practice("source", "functions"), 0),
    ("Fuente: complejidad media (final)", _practice("source", "meanComplexity", "after"), -1),
    ("Fuente: complejidad máxima (final)", _practice("source", "maxComplexity", "after"), -1),
    (
        "Fuente: funciones con complejidad > 10 (nuevas)",
        _practice("source", "complexFunctions"),
        -1,
    ),
    (
        "Fuente: función más larga, líneas (final)",
        _practice("source", "maxFunctionLength", "after"),
        -1,
    ),
    ("Fuente: funciones de más de 50 líneas (nuevas)", _practice("source", "longFunctions"), -1),
    (
        "Fuente: ventanas de 6 líneas duplicadas (nuevas)",
        _practice("source", "duplicatedWindows"),
        -1,
    ),
    ("Fuente: `except:` desnudo (nuevos)", _practice("source", "bareExcept"), -1),
    ("Fuente: `except Exception` amplio (nuevos)", _practice("source", "broadExcept"), -1),
    ("Fuente: errores tragados (nuevos)", _practice("source", "swallowedErrors"), -1),
    (
        "Fuente: `raise` en `except` con `from` (nuevos)",
        _practice("source", "raiseInExceptWithFrom"),
        1,
    ),
    (
        "Fuente: `raise` en `except` sin `from` (nuevos)",
        _practice("source", "raiseInExceptWithoutFrom"),
        -1,
    ),
    (
        "Fuente: cobertura de anotaciones de tipo (final)",
        _practice("source", "typeHintCoverage", "after"),
        1,
    ),
    (
        "Fuente: cobertura de docstrings públicas (final)",
        _practice("source", "docstringCoverage", "after"),
        1,
    ),
    ("Fuente: números mágicos (nuevos)", _practice("source", "magicNumbers"), -1),
    ("Fuente: sentencias inalcanzables (nuevas)", _practice("source", "unreachableStatements"), -1),
    ("Fuente: `raise` (validación de entrada, nuevos)", _practice("source", "raiseStatements"), 0),
    (
        "Fuente: `isinstance` (validación de entrada, nuevos)",
        _practice("source", "isinstanceChecks"),
        0,
    ),
    ("Fuente: nombres fuera de PEP 8 (nuevos)", _practice("source", "namingViolations"), -1),
    ("Fuente: nombres de una letra (nuevos)", _practice("source", "singleLetterNames"), -1),
    ("Pruebas: funciones de prueba (nuevas, netas)", _practice("tests", "tests"), 0),
    ("Pruebas: aserciones por prueba (final)", _practice("tests", "assertionsPerTest", "after"), 1),
    ("Pruebas: pruebas sin aserción (nuevas)", _practice("tests", "testsWithoutAssertion"), -1),
    ("Pruebas: pruebas negativas con `raises` (nuevas)", _practice("tests", "negativeTests"), 1),
    ("Pruebas: casos parametrizados (nuevos)", _practice("tests", "parametrizedCases"), 1),
    (
        "Pruebas: literales de borde (vacío, None, negativos, no ASCII; nuevos)",
        _practice("tests", "edgeCaseLiterals"),
        1,
    ),
    ("Pruebas: `global` en pruebas (nuevos)", _practice("tests", "globalStatements"), -1),
    (
        "Pruebas: pruebas que llaman a otras pruebas (nuevas)",
        _practice("tests", "testsCallingTests"),
        -1,
    ),
    (
        "Pruebas: escrituras a estado mutable compartido (nuevas)",
        _practice("tests", "sharedMutableStateWrites"),
        -1,
    ),
]


# A9: the five primary metrics, declared before the campaign (T3). Same getters and directions as
# the rows they name; only these feed the for/against tally. Every other row is exploratory.
PRIMARY_LABELS = (
    "Archivos fuera del alcance",
    "Ruff: hallazgos nuevos en fuente",
    "Pruebas: pruebas negativas con `raises` (nuevas)",
    "Cobertura de líneas fuente cambiadas",
    "Fuente: complejidad máxima (final)",
)
_ALL_ROWS = {row[0]: row for row in ROWS + PRACTICE_ROWS}
PRIMARY: list[Row] = [_ALL_ROWS[label] for label in PRIMARY_LABELS]
UNKNOWNS_ROW: Row = (
    "Qué hizo ante lo que no sabía (preguntó > declaró supuestos > inventó)",
    lambda r: (
        None
        if _get(r, "requirements.unknowns.behavior") is None
        else (
            f"{_get(r, 'requirements.unknowns.behavior')} "
            f"({_get(r, 'requirements.unknowns.inventedValues')} valores inventados)"
            if _get(r, "requirements.unknowns.behavior") == INVENTED
            else _get(r, "requirements.unknowns.behavior")
        )
    ),
    0,
)
EXPLORATORY_ROWS: list[Row] = [r for r in ROWS if r[0] not in PRIMARY_LABELS]
EXPLORATORY_PRACTICE_ROWS: list[Row] = [r for r in PRACTICE_ROWS if r[0] not in PRIMARY_LABELS]


def _get(record: dict[str, Any], dotted: str) -> Any:
    value: Any = record
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _sum(record: dict[str, Any], *paths: str) -> Any:
    values = [_get(record, p) for p in paths]
    return None if all(v is None for v in values) else sum(v or 0 for v in values)


def _len(record: dict[str, Any], dotted: str) -> Any:
    value = _get(record, dotted)
    return len(value) if isinstance(value, list) else None


def _first(record: dict[str, Any], *paths: str) -> Any:
    return next((v for v in (_get(record, p) for p in paths) if v is not None), None)


def _dict_sum(record: dict[str, Any], dotted: str) -> Any:
    value = _get(record, dotted)
    return sum(value.values()) if isinstance(value, dict) else None


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _favours(a: Any, b: Any, polarity: int) -> str | None:
    """``"b"`` when the second value is the better one under the polarity, ``"a"`` the first, None
    when no direction is declared or the values are not comparable."""
    if polarity == 0:
        return None
    if isinstance(a, bool) and isinstance(b, bool):
        return "b" if (b if polarity > 0 else not b) else "a"
    if isinstance(a, int | float) and isinstance(b, int | float):
        return "b" if (b - a) * polarity > 0 else "a"
    return None


def differences(
    direct: dict[str, Any], other: dict[str, Any], rows: list[Row]
) -> tuple[list[str], Counter[str]]:
    """One line per row whose value differs between the two runs, with the side the row's declared
    polarity favours and nothing else. Returns the lines and the tally per side."""
    name = other["run"]["condition"]
    lines = []
    tally: Counter[str] = Counter()
    for label, getter, polarity in rows:
        a, b = getter(direct), getter(other)
        if a is None or b is None or a == b:
            continue
        side = _favours(a, b, polarity)
        winner = {"a": "direct", "b": name, None: "neutral"}[side]
        tally[winner] += 1
        note = "" if side is None else f"; a favor de `{winner}`"
        if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool):
            delta = round(b - a, 4)
            sign = "+" if delta > 0 else ""
            lines.append(
                f"- {label}: `{name}` {_cell(b)} frente a {_cell(a)} de `direct` ({sign}{_cell(delta)}{note})."
            )
        else:
            lines.append(
                f"- {label}: `{name}` {_cell(b)}, `direct` {_cell(a)}{' (' + note[2:] + ')' if note else ''}."
            )
    return lines, tally


def group_key(record: dict[str, Any]) -> tuple[int, str, str, str, int]:
    run = record["run"]
    return (
        PROMPT_ORDER.get(run["prompt"], 1),
        run["scenario"],
        run["prompt"],
        run["model"],
        run["rep"],
    )


def _table(runs: list[dict[str, Any]], rows: list[Row]) -> list[str]:
    out = [
        "| Métrica | " + " | ".join(f"`{r['run']['condition']}`" for r in runs) + " |",
        "|---|" + "---|" * len(runs),
    ]
    for label, getter, _polarity in rows:
        values = [getter(r) for r in runs]
        if all(v is None for v in values):
            continue
        out.append(f"| {label} | " + " | ".join(_cell(v) for v in values) + " |")
    return [*out, ""]


def _excerpt(record: dict[str, Any]) -> list[str]:
    if record.get("notMeasured"):
        return [f"`{record['run']['condition']}`: {record['run']['status']}, sin cambios.", ""]
    code = record.get("code") or {}
    if not code.get("text"):
        return [f"`{record['run']['condition']}`: sin cambios en archivos fuente.", ""]
    cut = f" (primeras {EXCERPT_LINES} de {code['lines']} líneas)" if code.get("truncated") else ""
    return [f"`{record['run']['condition']}`{cut}:", "", "```diff", code["text"], "```", ""]


CONDITION_ORDER = [
    "direct",
    "direct-po",
    "harness-core",
    "harness",
    "harness-nopo",
    "harness-nopanel",
    "harness-anchored",
    "clarify",
]


def _condition_rank(record: dict[str, Any]) -> int:
    condition = record["run"]["condition"]
    return (
        CONDITION_ORDER.index(condition) if condition in CONDITION_ORDER else len(CONDITION_ORDER)
    )


def comparison_markdown(records: list[dict[str, Any]], skipped: list[str]) -> str:
    groups: dict[tuple[int, str, str, str, int], list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(group_key(record), []).append(record)
    out = [
        "# Comparación de implementaciones",
        "",
        "Generado por `evaluation/analysis/compare_implementations.py` (determinista, sin llamadas a",
        "modelos). Métricas declaradas en la enmienda del 2026-11-11 de",
        "`evaluation/campaign/PREREGISTRO.md`. «—» indica que la métrica no aplica o no está disponible.",
        "Los prompts pobres y casuales van primero (comparación principal); el prompt completo después",
        "(secundaria). Las cinco métricas primarias (`PRIMARY`) van primero y solo sobre ellas se cuenta",
        "a favor y en contra; el resto es exploratorio y se lista sin entrar en el recuento. «A favor",
        "de» sigue solo la dirección declarada de cada métrica (más es mejor o menos es mejor); las",
        "métricas sin dirección declarada se listan sin lado. Los conteos de prácticas son lo que el",
        "cambio añadió (final menos base) y crecen con el tamaño del cambio.",
        "",
    ]
    section = None
    for key in sorted(groups):
        primary = key[0] < PROMPT_ORDER["full"]
        if section != primary:
            section = primary
            title = (
                "Prompts pobres y casuales (comparación principal)"
                if primary
                else "Prompt completo (comparación secundaria)"
            )
            out += [f"## {title}", ""]
        runs = sorted(groups[key], key=_condition_rank)
        _, scenario, prompt, model, rep = key
        out += [f"### {scenario} · prompt `{prompt}` · `{model}` · r{rep}", ""]
        direct = next((r for r in runs if r["run"]["condition"] == "direct"), None)
        if direct is None:
            out += [
                (
                    "**Falta la corrida `direct` de este prompt y modelo: no hay par que comparar; "
                    "se muestran solo las condiciones gobernadas.**"
                ),
                "",
            ]
        out += ["#### Métricas primarias", "", *_table(runs, [*PRIMARY, UNKNOWNS_ROW])]
        out += [
            "#### Resultado, cambio y requisitos (exploratorias)",
            "",
            *_table(runs, EXPLORATORY_ROWS),
        ]
        out += [
            "#### Buenas y malas prácticas (exploratorias)",
            "",
            *_table(runs, EXPLORATORY_PRACTICE_ROWS),
        ]
        out += ["#### Diferencias", ""]
        if direct is None:
            out += ["Sin par `direct`.", ""]
        for other in runs:
            if direct is None or other is direct:
                continue
            name = other["run"]["condition"]
            if other.get("notMeasured"):
                out += [
                    f"**`{name}` frente a `direct`**: {other['run']['status']} "
                    f"(`{other['run'].get('outcome')}`), sin llamadas al agente; no se mide ni se "
                    "compara (su árbol es la línea base).",
                    "",
                ]
                continue
            lines, tally = differences(direct, other, PRIMARY)
            extra, _ = differences(direct, other, EXPLORATORY_ROWS + EXPLORATORY_PRACTICE_ROWS)
            out += [
                f"**`{name}` frente a `direct`**: {tally[name]} de las 5 métricas primarias a "
                f"favor de `{name}`, {tally['direct']} a favor de `direct`, {tally['neutral']} "
                "distintas sin dirección declarada.",
                "",
                *(lines or [f"- `{name}` y `direct` coinciden en las métricas primarias."]),
                "",
                "Exploratorias (no entran en el recuento):",
                "",
                *(extra or [f"- `{name}` y `direct` coinciden en las exploratorias."]),
                "",
            ]
        out += [
            "#### Código",
            "",
            "Diff de los archivos fuente contra la base (contexto de 1 línea, recortado).",
            "",
        ]
        for record in runs:
            out += _excerpt(record)
    if skipped:
        out += [
            "## Directorios omitidos",
            "",
            "No tienen la forma de una corrida (sin `ws/` o con un nombre que no se reconoce):",
            "",
            *[f"- `{name}`" for name in sorted(skipped)],
            "",
        ]
    return "\n".join(out)


# ----------------------------------------------------------------------------- CLI


def depersonalize(text: str, run_dir: Path, work: Path | None) -> str:
    roots: list[Path] = [run_dir, EVALUATION]
    if work is not None:
        roots.append(work)
    return agentlib.anonymize(text, *roots)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--runs", type=Path, nargs="+", required=True, help="run directories or directories of runs"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-static", action="store_true", help="skip Ruff, Mypy and Bandit")
    args = parser.parse_args(argv)
    run_dirs, skipped = discover(args.runs)
    (args.out / "per-run").mkdir(parents=True, exist_ok=True)
    tools = {m: _tool_version(m) for m in ("ruff", "mypy", "bandit")} if not args.no_static else {}
    records = []
    for run_dir in run_dirs:
        record = analyze_run(run_dir, static=not args.no_static)
        record["tools"] = tools
        text = depersonalize(
            json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False),
            run_dir,
            run_dir.parent,
        )
        (args.out / "per-run" / f"{record['run']['id']}.json").write_text(
            text + "\n", encoding="utf-8", newline="\n"
        )
        records.append(json.loads(text))
        files = (record.get("change") or {}).get("files")
        print(json.dumps({"run": record["run"]["id"], "files": files}))
    markdown = comparison_markdown(records, skipped)
    (args.out / "comparison.md").write_text(markdown, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
