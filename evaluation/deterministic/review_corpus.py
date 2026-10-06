#!/usr/bin/env python3
"""N02-a: the deterministic parts of the review panel on a seeded corpus. No model by default.

Every case of ``evaluation/corpus/review/cases.yaml`` is a feature branch of the billing project
(``baseline/``) with one seeded defect per domain (quality, architecture, resilience, tests,
concurrency, pipeline security, instructions addressed to an agent, a credential, a rule a tool
verifies, a failing consistency check) or a clean change. The project runs with the configuration
``harness init`` writes (``review.panel``), one consistency check (``python -m compileall -q
src``) and two fixture reviewer providers that call no model
(``review_fixture_reviewer.py``). For each case:

1. ``silent``: ``harness review-code --mode manual --base main`` with reviewers that answer PASS
   without findings: what the harness finds by itself (rules a tool verifies, its own checks,
   consistency checks) per domain, and its findings on clean changes (false positives).
2. ``repeat``: the same review again: a global cache hit is expected (no reviewer called).
3. ``oracle``: ``--no-cache`` with reviewers that answer the seeded findings plus two decoys each
   (a location outside the diff and a rule outside the catalog without evidence): findings the
   harness drops (``droppedOutside``) and downgrades, against the decoys sent.
4. ``test-only``: a test-only commit on top and the silent review again: which reviewers ran
   and which answers came from the per-reviewer cache.

Real reviewers (N02-b): ``--reviewer-command '[\"python\", \"/path/adapter.py\", ...]'`` (a JSON
argv of a provider-protocol-1.1 command provider) replaces the silent fixture in steps 1, 2 and 4
and ``--reps N`` repeats step 1 without cache; ``--provider-env NAME`` passes environment
variables to it. The default makes no model call.

Usage:
  python evaluation/deterministic/review_corpus.py --out DIR [--harness PATH] [--work DIR]
      [--project-venv DIR] [--only id,...] [--reviewer-command JSON] [--reps 1]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    EVALUATION,
    HERE,
    Harness,
    add_common_arguments,
    environment,
    git,
    isolated_env,
    stamp,
    write_json,
    write_jsonl,
)

CORPUS = EVALUATION / "corpus" / "review"
FIXTURE = HERE / "review_fixture_reviewer.py"


def copy_baseline(target: Path) -> None:
    shutil.copytree(CORPUS / "baseline", target)
    (target / "gitignore.template").rename(target / ".gitignore")
    (target / "dot-github").rename(target / ".github")


def workspace_path(path: str) -> str:
    return ".github/" + path[len("dot-github/") :] if path.startswith("dot-github/") else path


def apply_edits(ws: Path, case: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for item in case.get("replace") or []:
        target = ws / workspace_path(item["path"])
        text = target.read_text()
        if item["old"] not in text:
            raise ValueError(f"{case['id']}: {item['old']!r} not in {item['path']}")
        target.write_text(text.replace(item["old"], item["new"], 1))
        changed.append(workspace_path(item["path"]))
    for path, content in (case.get("append") or {}).items():
        target = ws / workspace_path(path)
        target.write_text(target.read_text() + content)
        changed.append(workspace_path(path))
    for path, content in (case.get("write") or {}).items():
        target = ws / workspace_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        changed.append(workspace_path(path))
    return sorted(set(changed))


def locate_truth(ws: Path, case: dict[str, Any]) -> list[dict[str, Any]]:
    located = []
    for item in case.get("truth") or []:
        lines = (ws / item["path"]).read_text().splitlines()
        number = next(i for i, line in enumerate(lines, start=1) if item["match"] in line)
        located.append({**item, "line": number, "text": lines[number - 1].strip()})
    return located


def configure(ws: Path, run_dir: Path, args: argparse.Namespace, truth_file: Path) -> None:
    path = ws / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text())
    providers = config.setdefault("agentProviders", {}) or {}
    log = str(args.call_log_dir / f"{run_dir.name}.jsonl")
    providers["reviewer-silent"] = {
        "kind": "command",
        "command": ["python", str(FIXTURE), "--mode", "silent", "--log", log],
    }
    providers["reviewer-oracle"] = {
        "kind": "command",
        "command": ["python", str(FIXTURE), "--mode", "oracle", "--truth", str(truth_file)],
    }
    if args.reviewer_command:
        real: dict[str, Any] = {"kind": "command", "command": json.loads(args.reviewer_command)}
        if args.reviewer_model:
            real["model"] = args.reviewer_model
        if args.provider_env:
            real["passEnv"] = args.provider_env
        providers["reviewer-real"] = real
    config["agentProviders"] = providers
    config["review"]["panel"]["consistencyChecks"] = [
        {"id": "compile", "command": ["python", "-m", "compileall", "-q", "src"]}
    ]
    path.write_text(yaml.safe_dump(config, sort_keys=False))


def review(h: Harness, ws: Path, label: str, provider: str, *extra: str) -> dict[str, Any]:
    code, report, seconds = h.run(
        ws,
        "review-code",
        "--path",
        ".",
        "--mode",
        "manual",
        "--base",
        "main",
        "--provider",
        provider,
        "--json",
        *extra,
        label=label,
    )
    report = report if isinstance(report, dict) else {"raw": report}
    return {"exitCode": code, "seconds": round(seconds, 2), "report": report}


def matches(finding: dict[str, Any], truth: dict[str, Any]) -> bool:
    if finding.get("file") != truth["path"]:
        return False
    return finding.get("line") == truth["line"] or finding.get("rule") == truth["rule"]


def digest_report(result: dict[str, Any], truth: list[dict[str, Any]]) -> dict[str, Any]:
    report = result["report"]
    findings = report.get("findings") or []
    reviewers = report.get("reviewers") or []
    consistency = report.get("consistency") or []
    detected = []
    for item in truth:
        if item["domain"] == "consistency":
            hit = any(c.get("status") not in {"PASSED", "PASS"} for c in consistency)
            detected.append(
                {"rule": item["rule"], "detected": hit, "by": "consistency" if hit else None}
            )
            continue
        hits = [f for f in findings if matches(f, item)]
        detected.append(
            {
                "rule": item["rule"],
                "detected": bool(hits),
                "by": sorted({str(f.get("source")) for f in hits}) or None,
                "severity": sorted({str(f.get("severity")) for f in hits}) or None,
            }
        )
    extra = [
        {k: f.get(k) for k in ("file", "line", "rule", "severity", "source", "inCatalog")}
        for f in findings
        if not any(matches(f, item) for item in truth)
    ]
    sent = {"outside": 0, "outOfCatalog": 0}
    for item in reviewers:
        try:
            decoys = json.loads(item.get("summary") or "{}").get("decoys") or {}
        except (json.JSONDecodeError, AttributeError):
            decoys = {}
        for key in sent:
            sent[key] += int(decoys.get(key) or 0)
    counts = report.get("counts") or {}
    return {
        "exitCode": result["exitCode"],
        "seconds": result["seconds"],
        "verdict": report.get("verdict"),
        "blocking": report.get("blocking"),
        "detected": detected,
        "extraFindings": extra,
        "counts": counts,
        "cache": report.get("cache"),
        "tokens": report.get("tokens"),
        "reviewers": [
            {
                k: item.get(k)
                for k in (
                    "id",
                    "status",
                    "reason",
                    "cache",
                    "attempts",
                    "droppedOutside",
                    "downgraded",
                    "findings",
                )
            }
            for item in reviewers
        ],
        "consistency": [{k: c.get(k) for k in ("id", "status")} for c in consistency],
        "deterministic": report.get("deterministic"),
        "decoysSent": sent,
    }


def run_case(
    case: dict[str, Any], corpus: dict[str, Any], args: argparse.Namespace
) -> dict[str, Any]:
    run_dir = args.work / f"review-{case['id']}-{stamp()}"
    ws = run_dir / "ws"
    copy_baseline(ws)
    git(ws, "init", "-q", "-b", "main")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "baseline")
    h = Harness(args.harness, isolated_env([args.project_venv / "bin"], run_dir / "harness-state"))
    h.run(ws, "init", "--path", ".", label="init")
    if git(ws, "status", "--porcelain").strip():
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "harness init")
    git(ws, "checkout", "-q", "-b", "change")
    changed = apply_edits(ws, case)
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", f"change: {case['id']}")
    truth = locate_truth(ws, case)
    truth_file = run_dir / "truth.json"
    truth_file.write_text(json.dumps(truth))
    configure(ws, run_dir, args, truth_file)
    provider = "reviewer-real" if args.reviewer_command else "reviewer-silent"
    record: dict[str, Any] = {
        "case": case["id"],
        "seeded": bool(truth),
        "changedFiles": changed,
        "truth": truth,
    }
    if args.catalog_out and not args.catalog_out.exists():
        _, shown, _ = h.run(
            ws, "review", "rules", "show", "--path", ".", "--json", label="rules show"
        )
        write_json(args.catalog_out, shown)
    log = args.call_log_dir / f"{run_dir.name}.jsonl"

    def calls() -> int:
        return len(log.read_text().splitlines()) if log.exists() else 0

    before = calls()
    record["silent"] = digest_report(review(h, ws, "review", provider), truth)
    record["silent"]["fixtureCalls"] = calls() - before
    before = calls()
    record["repeat"] = digest_report(review(h, ws, "review again", provider), truth)
    record["repeat"]["fixtureCalls"] = calls() - before
    record["oracle"] = digest_report(
        review(h, ws, "review oracle", "reviewer-oracle", "--no-cache"), truth
    )
    for rep in range(2, args.reps + 1):
        record.setdefault("variance", []).append(
            digest_report(review(h, ws, f"review rep {rep}", provider, "--no-cache"), truth)
        )
    follow = corpus["testOnlyFollowUp"]
    (ws / follow["path"]).write_text(follow["content"])
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "test-only follow-up")
    before = calls()
    record["testOnly"] = digest_report(
        review(h, ws, "review after a test-only change", provider), truth
    )
    record["testOnly"]["fixtureCalls"] = calls() - before
    record["steps"] = h.steps
    silent = record["silent"]
    print(
        case["id"],
        silent["verdict"],
        [d["detected"] for d in silent["detected"]],
        len(silent["extraFindings"]),
        record["repeat"]["cache"],
        record["oracle"]["counts"].get("droppedOutside"),
        flush=True,
    )
    return record


def summarize(records: list[dict[str, Any]], catalog: dict[str, Any] | None) -> dict[str, Any]:
    rules: dict[str, dict[str, Any]] = {}
    for rule in (catalog or {}).get("rules", []) if isinstance(catalog, dict) else []:
        if isinstance(rule, dict) and rule.get("id"):
            rules[rule["id"]] = rule
    per_domain: dict[str, dict[str, Any]] = {}
    for record in records:
        for item, detected in zip(record["truth"], record["silent"]["detected"], strict=True):
            rule = rules.get(item["rule"], {})
            raw = (
                rule.get("verifiedBy")
                or rule.get("verified_by")
                or ([item["rule"]] if item["rule"].startswith("tool:") else [])
            )
            verified = ",".join(raw) if isinstance(raw, list) else str(raw)
            reviewer = next(
                (x for x in record["silent"]["reviewers"] if x.get("id") == item["domain"]), {}
            )
            bucket = per_domain.setdefault(
                item["domain"],
                {"seeded": 0, "detectedDeterministic": 0, "toolVerifiedRules": 0, "cases": []},
            )
            bucket["seeded"] += 1
            bucket["detectedDeterministic"] += int(bool(detected["detected"]))
            bucket["toolVerifiedRules"] += int(
                verified.startswith("tool:") or item["rule"].startswith(("tool:", "consistency:"))
            )
            bucket["cases"].append(
                {
                    "case": record["case"],
                    "rule": item["rule"],
                    "detected": detected["detected"],
                    "by": detected["by"],
                    "verifiedBy": verified or None,
                    "domainReviewer": reviewer.get("status"),
                    "domainReviewerReason": reviewer.get("reason"),
                }
            )
    clean = [r for r in records if not r["seeded"]]
    seeded = [r for r in records if r["seeded"]]
    oracle_sent = {
        k: sum(r["oracle"]["decoysSent"][k] for r in records) for k in ("outside", "outOfCatalog")
    }
    return {
        "cases": len(records),
        "seededCases": len(seeded),
        "cleanCases": len(clean),
        "deterministicDetectionByDomain": per_domain,
        "deterministicDetected": sum(b["detectedDeterministic"] for b in per_domain.values()),
        "seededFindings": sum(b["seeded"] for b in per_domain.values()),
        "falsePositives": {
            "cleanCasesWithAnyFinding": sum(1 for r in clean if r["silent"]["extraFindings"]),
            "cleanCasesWithBlockingVerdict": sum(
                1 for r in clean if r["silent"]["verdict"] in {"FAIL", "UNKNOWN"}
            ),
            "findingsOnCleanCases": [
                f | {"case": r["case"]} for r in clean for f in r["silent"]["extraFindings"]
            ],
            "extraFindingsOnSeededCases": [
                f | {"case": r["case"]} for r in seeded for f in r["silent"]["extraFindings"]
            ],
        },
        "locationFiltering": {
            "decoysOutsideSent": oracle_sent["outside"],
            "droppedOutside": sum(
                int(r["oracle"]["counts"].get("droppedOutside") or 0) for r in records
            ),
            "decoysOutOfCatalogSent": oracle_sent["outOfCatalog"],
            "downgraded": sum(int(r["oracle"]["counts"].get("downgraded") or 0) for r in records),
            "seededFindingsKeptInOracleMode": sum(
                int(bool(d["detected"])) for r in records for d in r["oracle"]["detected"]
            ),
        },
        "cache": {
            "identicalReReviewGlobalHits": sum(
                1 for r in records if (r["repeat"]["cache"] or {}).get("global") == "hit"
            ),
            "identicalReReviewTokens": sum(
                int((r["repeat"]["tokens"] or {}).get("total") or 0) for r in records
            ),
            "identicalReReviewFixtureCalls": sum(
                int(r["repeat"].get("fixtureCalls") or 0) for r in records
            ),
            "firstReviewFixtureCalls": sum(
                int(r["silent"].get("fixtureCalls") or 0) for r in records
            ),
            "testOnlyReviewFixtureCalls": sum(
                int(r["testOnly"].get("fixtureCalls") or 0) for r in records
            ),
            "identicalReReviewModelCalls": sum(
                int((r["repeat"]["tokens"] or {}).get("modelCalls") or 0) for r in records
            ),
            "firstReviewModelCalls": sum(
                int((r["silent"]["tokens"] or {}).get("modelCalls") or 0) for r in records
            ),
            "testOnlyReviewModelCalls": sum(
                int((r["testOnly"]["tokens"] or {}).get("modelCalls") or 0) for r in records
            ),
            "testOnlyReviewerCacheHits": sum(
                int((r["testOnly"]["cache"] or {}).get("reviewerHits") or 0) for r in records
            ),
            "testOnlyReviewersRun": sorted(
                {
                    f"{item['id']}"
                    for r in records
                    for item in r["testOnly"]["reviewers"]
                    if item.get("status") not in {"SKIPPED"} and item.get("cache") != "hit"
                }
            ),
        },
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
    parser.add_argument(
        "--reviewer-command", default="", help="JSON argv of a real reviewer provider (N02-b)"
    )
    parser.add_argument("--reviewer-model", default="")
    parser.add_argument("--provider-env", action="append", default=[])
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.work = (args.work or args.out / ".work").resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    args.catalog_out = args.out / "review-catalog.json"
    # The fixture's call log lives where the reviewers' read-only sandbox lets them write.
    args.call_log_dir = Path(tempfile.mkdtemp(prefix="review-fixture-calls-"))
    corpus = yaml.safe_load((CORPUS / "cases.yaml").read_text())
    records_path = args.out / "review-corpus.jsonl"
    if not args.summarize_only:
        cases = [c for c in corpus["cases"] if not args.only or c["id"] in args.only.split(",")]
        write_json(
            args.out / "environment.json",
            environment(
                args.harness,
                args.wheel,
                {
                    "suite": "N02-a review corpus",
                    "reviewer": "real: " + args.reviewer_command
                    if args.reviewer_command
                    else "fixture (no model)",
                    "cases": [c["id"] for c in cases],
                    "reps": args.reps,
                    "projectVenv": str(args.project_venv),
                },
            ),
        )
        if args.catalog_out.exists():
            args.catalog_out.unlink()
        records = [run_case(case, corpus, args) for case in cases]
        write_jsonl(records_path, records)
    records = [json.loads(line) for line in records_path.read_text().splitlines() if line.strip()]
    catalog = json.loads(args.catalog_out.read_text()) if args.catalog_out.exists() else None
    write_json(args.out / "review-summary.json", summarize(records, catalog))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
