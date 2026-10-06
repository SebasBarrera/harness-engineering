#!/usr/bin/env python3
"""Apply the harness's deterministic review rules to the final change of every run.

The harness condition applies these rules during INDEPENDENT_REVIEW; the baseline condition has no
review. Scanning the final change of both conditions with the same rules shows what each condition
delivers. For harness runs, the findings raised during the run are also listed with their file.

Since the 2.0.0 evaluation the run directories of every condition are read (direct, harness-core,
harness, harness-tiered and the 0.9.0 names), with the prompt level, from ``<runs>/<run>`` or
``<runs>/<model>/<run>``; the findings raised during a run are read from the workspace's state
database or from the run registry in the run directory. A stopped run whose change was quarantined is
scanned on its quarantine copy when ``run_eval.py`` left one.

Usage: python posthoc_review.py <runs-dir> <out.jsonl>
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from governed_harness.validators.review import RULES

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_state import state_db  # noqa: E402

NAME = re.compile(
    r"^(greenfield|brownfield|security)(?:-(poor|casual))?"
    r"-(baseline|direct|harness-core|harness-tiered|harness|clarify)-(.+)-r(\d+)-\d{8}T\d{6}Z$"
)
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def added_lines(workspace: Path) -> list[tuple[str, str]]:
    base = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=workspace,
        env=GIT_ENV,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "add", "-A", "-N"], cwd=workspace, env=GIT_ENV, capture_output=True)
    diff = subprocess.run(
        ["git", "diff", "-U0", base, "--", ".", ":(exclude).harness"],
        cwd=workspace,
        env=GIT_ENV,
        capture_output=True,
        text=True,
    ).stdout
    current, lines = None, []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("+") and current and "__pycache__" not in current:
            lines.append((current, line[1:]))
    return lines


def main() -> int:
    runs, out = Path(sys.argv[1]), Path(sys.argv[2])
    with out.open("w", encoding="utf-8") as handle:
        for run_dir in sorted(p for p in {*runs.glob("*"), *runs.glob("*/*")} if p.is_dir()):
            match = NAME.match(run_dir.name)
            if not match or not (run_dir / "ws").exists():
                continue
            scenario, prompt, condition, model, rep = match.groups()
            workspace = (
                run_dir / "quarantine-ws"
                if (run_dir / "quarantine-ws").exists()
                else run_dir / "ws"
            )
            final_hits = []
            for path, content in added_lines(workspace):
                for rule in RULES:
                    if rule.pattern.search(content):
                        final_hits.append(
                            {
                                "rule": rule.rule_id,
                                "severity": rule.severity.value,
                                "file": path,
                                "isTest": path.startswith("tests/"),
                                "devPasswordLiteral": "dev-Alerts-2026!" in content,
                                "snippet": content.strip()[:70],
                            }
                        )
            during = []
            state = state_db(run_dir / "ws", run_dir)
            if state is not None:
                db = sqlite3.connect(state)
                for (payload,) in db.execute(
                    "select payload_json from records where record_type='finding'"
                ):
                    finding = json.loads(payload)
                    if finding["ruleId"].startswith("review."):
                        during.append(
                            {
                                "rule": finding["ruleId"],
                                "severity": finding["severity"],
                                "file": (finding.get("location") or {}).get("path"),
                            }
                        )
                db.close()
            record = {
                "scenario": scenario,
                "prompt": prompt or "full",
                "condition": condition,
                "model": model,
                "rep": int(rep),
                "finalChangeHits": final_hits,
                "reviewFindingsDuringRun": during,
            }
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
