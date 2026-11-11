#!/usr/bin/env python3
"""H46: compare the deterministic suites across platforms against the pre-registered list of
expected differences (``evaluation/campaign/PREREGISTRO.md``, H46). No suite is re-run.

Each platform directory holds the results of one platform as ``run_deterministic.py`` writes them
(``fault-probes/fault-probes-summary.json``, ``friction/friction.jsonl``,
``ladder/ladder-corpus.jsonl``, ``review/review-corpus.jsonl``) plus the transcript of
``scripts/demo_flows.py all`` (``demo/demo-transcript.json``). Every platform is compared with the
reference (``linux-bwrap``, the environment of rule 6) test by test: a probe, a friction run, a
ladder or review case, a demo step. A test *matches* when every compared field is equal after
identifiers, digests, instants and absolute paths are normalized; the measures the
pre-registration excludes (seconds, estimated tokens, artifact sizes, free text) are not compared.
A differing test is *expected* when a rule of ``EXPECTED`` (E1-E4 of the pre-registration) covers
it, otherwise *unexpected*.

The summary (``portability-summary.json``) gives per platform and suite the matches out of the
tests, the expected and unexpected differences, the expected differences not observed and the
tests that record ``sandbox.unavailable``. The script exits 1 when there is an unexpected
difference or a platform or suite is absent (that suite is then "not evaluated"), so a partial
comparison is never read as a full one.

Usage:
  python evaluation/deterministic/portability_compare.py --out DIR \\
      --platform linux-bwrap=DIR --platform linux-nobwrap=DIR --platform macos=DIR \\
      --platform windows=DIR [--reference linux-bwrap]
  python evaluation/deterministic/portability_compare.py --self-check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PLATFORMS = ("linux-bwrap", "linux-nobwrap", "macos", "windows")
REFERENCE = "linux-bwrap"
SUITES = ("fault-probes", "friction", "ladder", "review", "demo")
SANDBOX_MARKERS = ("sandbox.unavailable", "sandbox is enforced but unavailable")
# Measures the pre-registration excludes from the comparison (recorded, not compared).
EXCLUDED = {
    "seconds",
    "commandSeconds",
    "runWallSeconds",
    "recordedTokens",
    "tokens",
    "providerArtifactBytes",
    "stdout",
    "stderr",
    "steps",
}


@dataclass(frozen=True)
class Rule:
    id: str
    platforms: frozenset[str]
    suites: frozenset[str]
    cases: frozenset[str] | None
    """``None``: any test of the suites, provided its record carries the sandbox marker."""
    statement: str

    def covers(self, platform: str, suite: str, case: str, raw: str) -> bool:
        if platform not in self.platforms or suite not in self.suites:
            return False
        if self.cases is None:
            return any(marker in raw for marker in SANDBOX_MARKERS)
        return case in self.cases


EXPECTED = (
    Rule(
        "E1",
        frozenset({"linux-nobwrap"}),
        frozenset({"fault-probes", "friction", "review"}),
        None,
        "linux-nobwrap: enforce without bwrap refuses every command provider (sandbox.unavailable) "
        "and leaves command reviewers UNKNOWN; covers a differing test whose record carries it",
    ),
    Rule(
        "E2",
        frozenset({"windows"}),
        frozenset({"fault-probes"}),
        frozenset({"write-outside", "write-git-hooks", "write-symlink"}),
        "windows: agentSandbox off, nothing denies the writes outside the workspace",
    ),
    Rule(
        "E3",
        frozenset({"windows"}),
        frozenset({"fault-probes"}),
        frozenset({"lease-sigterm"}),
        "windows: SIGTERM is TerminateProcess, the harness's handler does not run",
    ),
    Rule(
        "E4",
        frozenset({"windows"}),
        frozenset({"fault-probes"}),
        frozenset({"timeout"}),
        "windows (possible): descendants of a timed-out provider are not terminated",
    ),
)

_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(BLOCKING_FINDING|EXCEPTION_APPLIED)_\S+"), r"\1_<id>"),
    (re.compile(r"sha256:[0-9a-f]{8,64}"), "<digest>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<id>"),
    (re.compile(r"\b[a-z]+_[0-9a-f]{12,}\b"), "<id>"),
    (re.compile(r"\b[0-9a-f]{16,}\b"), "<hex>"),
    (re.compile(r"\d{4}-\d\d-\d\dT[0-9:.]+(?:Z|[+-]\d\d:\d\d)?"), "<instant>"),
    (re.compile(r"\b\d{8}T\d{6}Z\b"), "<instant>"),
    # Absolute paths: the placeholders of sanitize_paths.py, Windows drives and POSIX roots.
    (re.compile(r"(?:<work>|<tmp>|<repo>|~)(?:[\\/][^\s\"'<>|,;]*)?"), "<path>"),
    (re.compile(r"\b[A-Za-z]:[\\/][^\s\"'<>|,;]*"), "<path>"),
    (re.compile(r"(?<![\w.<>-])/[^\s\"'<>|,;]+"), "<path>"),
)


def normalize(value: Any) -> Any:
    """The value without what changes by design between runs and machines."""
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items() if k not in EXCLUDED}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, str):
        for pattern, placeholder in _REPLACEMENTS:
            value = pattern.sub(placeholder, value)
        return value
    return value


# ----- the tests of each suite -----------------------------------------------------------------

Cases = dict[str, dict[str, Any]]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def fault_probe_cases(directory: Path) -> Cases:
    summary = json.loads(
        (directory / "fault-probes" / "fault-probes-summary.json").read_text(encoding="utf-8")
    )
    return {
        name: {
            "identical": probe.get("identical"),
            "matchesExpectation": probe.get("matchesExpectation"),
            **{f"observed.{k}": v for k, v in (probe.get("observed") or {}).items()},
        }
        for name, probe in summary["probes"].items()
    }


FRICTION_FIELDS = (
    "runExitCode",
    "approveExitCode",
    "status",
    "phase",
    "gate",
    "delivered",
    "deliveredWithoutAPerson",
    "laneClassified",
    "laneEscalated",
    "laneReported",
    "preAuthorization",
    "blockingFindings",
    "commandsTyped",
    "commands",
    "humanInteractions",
    "recordedCalls",
    "callsByKind",
    "fixtureCalls",
)


def friction_cases(directory: Path) -> Cases:
    return {
        f"{r['condition']}/{r['task']}/{r['risk']}/r{r['rep']}": {
            k: r.get(k) for k in FRICTION_FIELDS
        }
        for r in read_jsonl(directory / "friction" / "friction.jsonl")
    }


LADDER_FIELDS = (
    "taskCreateExitCode",
    "runExitCode",
    "waits",
    "runStatus",
    "phase",
    "gate",
    "gateReasons",
    "certification",
    "criteria",
    "preflight",
    "findings",
    "mutationFindings",
    "testQualityFindings",
    "certificationFindings",
    "oracle",
    "defectiveObserved",
    "truthConsistent",
    "signals",
)


def ladder_cases(directory: Path) -> Cases:
    cases: Cases = {}
    for r in read_jsonl(directory / "ladder" / "ladder-corpus.jsonl"):
        fields = {k: r.get(k) for k in LADDER_FIELDS}
        # The evidence of a criterion names files and commands of this machine.
        fields["criteria"] = {
            cid: {k: v for k, v in (item or {}).items() if k != "evidence"}
            for cid, item in (r.get("criteria") or {}).items()
        }
        cases[f"{r['case']}/r{r['rep']}"] = fields
    return cases


REVIEW_PARTS = ("silent", "repeat", "oracle", "testOnly", "variance")


def review_cases(directory: Path) -> Cases:
    return {
        r["case"]: {k: r.get(k) for k in ("seeded", "changedFiles", "truth", *REVIEW_PARTS)}
        for r in read_jsonl(directory / "review" / "review-corpus.jsonl")
    }


def demo_cases(directory: Path) -> Cases:
    steps = json.loads((directory / "demo" / "demo-transcript.json").read_text(encoding="utf-8"))
    cases: Cases = {}
    position: dict[str, int] = {}
    for step in steps:
        flow = str(step.get("flow"))
        position[flow] = position.get(flow, 0) + 1
        cases[f"{flow}#{position[flow]}"] = {k: step.get(k) for k in ("expected", "actual", "ok")}
    return cases


READERS: dict[str, Callable[[Path], Cases]] = {
    "fault-probes": fault_probe_cases,
    "friction": friction_cases,
    "ladder": ladder_cases,
    "review": review_cases,
    "demo": demo_cases,
}


def load(directory: Path, suite: str) -> tuple[Cases | None, str]:
    try:
        return READERS[suite](directory), ""
    except (OSError, ValueError, KeyError, TypeError) as error:
        return None, f"{type(error).__name__}: {error}"


# ----- the comparison --------------------------------------------------------------------------


def short(value: Any) -> Any:
    text = json.dumps(value, sort_keys=True)
    return value if len(text) <= 400 else text[:400] + "..."


def marked(case: dict[str, Any] | None) -> bool:
    raw = json.dumps(case, sort_keys=True) if case is not None else ""
    return any(marker in raw for marker in SANDBOX_MARKERS)


def compare_suite(platform: str, suite: str, reference: Cases, observed: Cases) -> dict[str, Any]:
    tests = sorted(set(reference) | set(observed))
    matches = 0
    expected: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    for case in tests:
        ref, obs = reference.get(case), observed.get(case)
        if ref is None or obs is None:
            fields = [
                {
                    "field": "<test>",
                    "reference": "absent" if ref is None else "present",
                    "observed": "absent" if obs is None else "present",
                }
            ]
        else:
            nref, nobs = normalize(ref), normalize(obs)
            fields = [
                {"field": key, "reference": short(nref.get(key)), "observed": short(nobs.get(key))}
                for key in sorted(set(nref) | set(nobs))
                if nref.get(key) != nobs.get(key)
            ]
        if not fields:
            matches += 1
            continue
        raw = json.dumps(obs, sort_keys=True) if obs is not None else ""
        rules = [rule.id for rule in EXPECTED if rule.covers(platform, suite, case, raw)]
        if rules:
            expected.append({"test": case, "rules": rules, "fields": [f["field"] for f in fields]})
        else:
            unexpected.extend({"test": case, **f} for f in fields)
    return {
        "status": "compared",
        "tests": len(tests),
        "matches": matches,
        "expectedDifferences": expected,
        "unexpectedDifferences": unexpected,
        "unexpectedTests": len({item["test"] for item in unexpected}),
        "sandboxUnavailable": sum(1 for case in observed.values() if marked(case)),
    }


def compare(platforms: dict[str, Path], reference: str) -> dict[str, Any]:
    loaded: dict[str, dict[str, tuple[Cases | None, str]]] = {
        name: {suite: load(directory, suite) for suite in SUITES}
        for name, directory in platforms.items()
    }
    absent = [name for name in PLATFORMS if name not in platforms]
    by_platform: dict[str, Any] = {}
    ref_suites = loaded.get(reference, {})
    for name in platforms:
        suites: dict[str, Any] = {}
        for suite in SUITES:
            observed, error = loaded[name][suite]
            ref, ref_error = ref_suites.get(suite, (None, "reference platform absent"))
            if observed is None or ref is None:
                suites[suite] = {
                    "status": "not evaluated",
                    "reason": error if observed is None else f"reference: {ref_error}",
                    "sandboxUnavailable": sum(1 for c in (observed or {}).values() if marked(c)),
                }
                continue
            if name == reference:
                suites[suite] = {
                    "status": "reference",
                    "tests": len(observed),
                    "sandboxUnavailable": sum(1 for c in observed.values() if marked(c)),
                }
                continue
            suites[suite] = compare_suite(name, suite, ref, observed)
        compared = [s for s in suites.values() if s["status"] == "compared"]
        by_platform[name] = {
            "suites": suites,
            "tests": sum(s["tests"] for s in compared),
            "matches": sum(s["matches"] for s in compared),
            "unexpectedDifferences": sum(len(s["unexpectedDifferences"]) for s in compared),
            "notEvaluated": sorted(k for k, s in suites.items() if s["status"] == "not evaluated"),
        }
    not_observed = []
    for rule in EXPECTED:
        for name in sorted(rule.platforms & set(platforms)):
            hits = {
                item["test"]
                for suite in rule.suites
                for item in (by_platform[name]["suites"].get(suite) or {}).get(
                    "expectedDifferences", []
                )
                if rule.id in item["rules"]
            }
            missing = (
                sorted(rule.cases - hits)
                if rule.cases is not None
                else ([] if hits else ["<any test with sandbox.unavailable>"])
            )
            if missing:
                not_observed.append({"rule": rule.id, "platform": name, "tests": missing})
    unexpected = sum(p["unexpectedDifferences"] for p in by_platform.values())
    not_evaluated = {n: p["notEvaluated"] for n, p in by_platform.items() if p["notEvaluated"]}
    return {
        "reference": reference,
        "platforms": sorted(platforms),
        "absentPlatforms": absent,
        "expectedDifferenceRules": {rule.id: rule.statement for rule in EXPECTED},
        "excludedMeasures": sorted(EXCLUDED),
        "byPlatform": by_platform,
        "expectedNotObserved": not_observed,
        "criterion": {
            "statement": "0 unexpected differences; the expected ones are documented (PREREGISTRO.md, H46)",
            "unexpectedDifferences": unexpected,
            "met": unexpected == 0 and not absent and not not_evaluated,
            "evaluable": not absent and not not_evaluated,
        },
    }


def table(summary: dict[str, Any]) -> str:
    lines = [
        "| platform | suite | matches / tests | expected | unexpected | sandbox.unavailable |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for name, platform in summary["byPlatform"].items():
        for suite, item in platform["suites"].items():
            if item["status"] != "compared":
                lines.append(
                    f"| {name} | {suite} | {item['status']} | | | {item['sandboxUnavailable']} |"
                )
                continue
            lines.append(
                f"| {name} | {suite} | {item['matches']} / {item['tests']} | "
                f"{len(item['expectedDifferences'])} | {item['unexpectedTests']} | "
                f"{item['sandboxUnavailable']} |"
            )
    return "\n".join(lines) + "\n"


# ----- self-check ------------------------------------------------------------------------------


def _write_platform(root: Path, name: str, mutate: Callable[[dict[str, Any]], None]) -> Path:
    data: dict[str, Any] = {
        "probes": {
            "correct": {
                "identical": True,
                "matchesExpectation": [True],
                "observed": {"final": {"status": "PASSED"}, "findings": []},
            },
            "lease-sigterm": {
                "identical": True,
                "matchesExpectation": [True],
                "observed": {"final": {"status": "PASSED"}, "findings": []},
            },
        },
        "friction": [
            {
                "condition": "full",
                "task": "t",
                "risk": "none",
                "rep": 1,
                "status": "PASSED",
                "recordedTokens": 10,
                "blockingFindings": [],
            }
        ],
        "ladder": [
            {
                "case": "c1",
                "rep": 1,
                "runStatus": "PASSED",
                "findings": [],
                "criteria": {"ac": {"status": "MET", "evidence": "/home/runner/x"}},
            }
        ],
        "review": [
            {
                "case": "r1",
                "seeded": True,
                "silent": {"verdict": "PASS", "seconds": 1.0, "reviewers": []},
            }
        ],
        "demo": [{"flow": "quickstart", "expected": 0, "actual": 0, "ok": True}],
    }
    mutate(data)
    directory = root / name
    (directory / "fault-probes").mkdir(parents=True)
    (directory / "fault-probes" / "fault-probes-summary.json").write_text(
        json.dumps({"probes": data["probes"]}), encoding="utf-8"
    )
    for suite, file in (
        ("friction", "friction.jsonl"),
        ("ladder", "ladder-corpus.jsonl"),
        ("review", "review-corpus.jsonl"),
    ):
        (directory / suite).mkdir()
        (directory / suite / file).write_text(
            "".join(json.dumps(r) + "\n" for r in data[suite]), encoding="utf-8"
        )
    (directory / "demo").mkdir()
    (directory / "demo" / "demo-transcript.json").write_text(
        json.dumps(data["demo"]), encoding="utf-8"
    )
    return directory


def self_check() -> int:
    def nothing(data: dict[str, Any]) -> None:
        return None

    def windows(data: dict[str, Any]) -> None:
        data["probes"]["lease-sigterm"]["matchesExpectation"] = [False]  # E3
        data["friction"][0]["recordedTokens"] = 99  # excluded measure
        data["ladder"][0]["criteria"]["ac"]["evidence"] = "C:\\w\\x"  # excluded
        data["review"][0]["silent"]["seconds"] = 9.0  # excluded
        data["demo"][0]["actual"] = 1  # unexpected
        data["demo"][0]["ok"] = False

    def nobwrap(data: dict[str, Any]) -> None:
        data["friction"][0].update(status="BLOCKED", blockingFindings=["HIGH:sandbox.unavailable"])
        data["review"][0]["silent"]["verdict"] = "FAIL"  # no marker: unexpected

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        platforms = {
            "linux-bwrap": _write_platform(root, "linux-bwrap", nothing),
            "macos": _write_platform(root, "macos", nothing),
            "windows": _write_platform(root, "windows", windows),
            "linux-nobwrap": _write_platform(root, "linux-nobwrap", nobwrap),
        }
        summary = compare(platforms, REFERENCE)
        by = summary["byPlatform"]
        assert by["macos"]["matches"] == by["macos"]["tests"] == 6, by["macos"]
        win = by["windows"]["suites"]
        assert win["fault-probes"]["expectedDifferences"][0]["rules"] == ["E3"], win
        assert win["friction"]["matches"] == 1 and win["ladder"]["matches"] == 1, win
        assert win["review"]["matches"] == 1, win
        assert [d["field"] for d in win["demo"]["unexpectedDifferences"]] == ["actual", "ok"], win
        nob = by["linux-nobwrap"]["suites"]
        assert nob["friction"]["expectedDifferences"][0]["rules"] == ["E1"], nob
        assert nob["friction"]["sandboxUnavailable"] == 1, nob
        assert nob["review"]["unexpectedTests"] == 1, nob
        assert {
            "rule": "E2",
            "platform": "windows",
            "tests": ["write-git-hooks", "write-outside", "write-symlink"],
        } in summary["expectedNotObserved"], summary["expectedNotObserved"]
        assert (
            summary["criterion"]["met"] is False
            and summary["criterion"]["unexpectedDifferences"] == 3
        )
        partial = compare({"linux-bwrap": platforms["linux-bwrap"]}, REFERENCE)
        assert partial["absentPlatforms"] == ["linux-nobwrap", "macos", "windows"], partial
        assert partial["criterion"]["evaluable"] is False
        assert normalize("C:\\a\\b and /home/x/y and src/a.py") == "<path> and <path> and src/a.py"
        assert normalize("src\\a.py") == "src\\a.py"
    print("self-check passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", action="append", default=[], help="NAME=DIR, repeatable")
    parser.add_argument("--reference", default=REFERENCE)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()
    if not args.out:
        parser.error("--out is required")
    platforms: dict[str, Path] = {}
    for item in args.platform:
        name, _, directory = item.partition("=")
        if name not in PLATFORMS or not directory:
            parser.error(
                f"--platform {item}: expected NAME=DIR with NAME in {', '.join(PLATFORMS)}"
            )
        platforms[name] = Path(directory)
    summary = compare(platforms, args.reference)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "portability-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (args.out / "portability.md").write_text(table(summary), encoding="utf-8", newline="\n")
    sys.stdout.write(table(summary))
    criterion = summary["criterion"]
    print(
        f"unexpected differences: {criterion['unexpectedDifferences']}; "
        f"absent platforms: {summary['absentPlatforms'] or 'none'}; "
        f"expected, not observed: {len(summary['expectedNotObserved'])}"
    )
    return 0 if criterion["met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
