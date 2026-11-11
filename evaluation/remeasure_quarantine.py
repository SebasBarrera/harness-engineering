#!/usr/bin/env python3
"""Measure the quarantined patch of stopped governed runs again from their run directories.

Before the fix of ``run_eval.measure_quarantine`` (amendment of 2026-11-11 to the
pre-registration), the patch of a stopped run was applied to the restored workspace, where the
kept acceptance tests already exist, and written in text mode (CRLF on Windows); when ``git apply``
failed the unpatched tree was measured. This script reads ``quarantined.patch`` of each run
directory, undoes the text-mode write (every LF had become CRLF, so CRLF -> LF restores the bytes
``harness artifact show`` printed), applies it to the baseline with ``run_eval.measure_patch`` and
prints one JSON line per run. No model call; the run's original files are not changed (the copy,
the patch and the scratch directory get the ``-remeasured`` suffix). Run it with the interpreter of
the evaluation environment: its site-packages receives the import path of the measured tree.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_eval  # noqa: E402

NAMES = (
    "quarantine-ws-remeasured",
    "quarantined-remeasured.patch",
    "measure-quarantine-remeasured",
)


def scenario_of(run_dir: Path) -> str:
    """The scenario is the first component of the run directory's name."""
    for scenario in sorted(run_eval.SCENARIOS, key=len, reverse=True):
        if run_dir.name.startswith(scenario + "-"):
            return scenario
    raise ValueError(f"no scenario in the name of {run_dir.name}")


def remeasure(run_dir: Path, cache: Path, site_packages: Path) -> dict[str, Any]:
    workspace = run_dir / "ws"
    # prepare() commits the baseline as the workspace's first commit.
    baseline = run_eval.git(workspace, "rev-list", "--max-parents=0", "HEAD").split()[0]
    raw = (run_dir / "quarantined.patch").read_bytes()
    pth = site_packages / "zz_eval_workspace.pth"
    previous = pth.read_bytes() if pth.is_file() else None
    try:
        measured = run_eval.measure_patch(
            scenario_of(run_dir),
            workspace,
            baseline,
            run_dir,
            cache,
            site_packages,
            raw.replace(b"\r\n", b"\n"),
            NAMES,
        )
    finally:
        if previous is None:
            pth.unlink(missing_ok=True)
        else:
            pth.write_bytes(previous)
    measured["patchApply"]["patchHadCrlf"] = b"\r\n" in raw
    return {"run": run_dir.name, "baseline": baseline, "measures": measured}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    site_packages = Path(next(p for p in sys.path if p.endswith("site-packages")))
    failed = 0
    for run_dir in args.run_dirs:
        result = remeasure(run_dir.resolve(), args.cache.resolve(), site_packages)
        failed += bool(result["measures"].get("invalid"))
        line = json.dumps(result, sort_keys=True)
        print(run_eval.agentlib.anonymize(line, run_dir.resolve(), HERE, run_dir.resolve().parent))
    return run_eval.MEASUREMENT_FAILED_EXIT_CODE if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
