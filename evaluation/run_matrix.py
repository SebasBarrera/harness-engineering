#!/usr/bin/env python3
"""Run the evaluation matrix for one model: scenarios x conditions x repetitions.

The order is counterbalanced per repetition with a fixed seed, so neither condition always runs
first. Each run is independent (fresh repository, fresh harness state); a failing run is logged
and the matrix continues.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def plan(model: str, reps: int, seed: int, scenarios: list[str]) -> list[tuple[str, str, int]]:
    rng = random.Random(f"{seed}:{model}")
    order: list[tuple[str, str, int]] = []
    for rep in range(1, reps + 1):
        cells = [(s, c, rep) for s in scenarios for c in ("baseline", "harness")]
        rng.shuffle(cells)
        order += cells
    return order


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--scenarios", default="greenfield,brownfield")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    done = set()
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            record = json.loads(line)
            done.add((record["scenario"], record["condition"], record["rep"]))
    for scenario, condition, rep in plan(args.model, args.reps, args.seed, args.scenarios.split(",")):
        if (scenario, condition, rep) in done:
            continue
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        print(f"{stamp} start {scenario} {condition} rep {rep}", flush=True)
        proc = subprocess.run(
            [sys.executable, str(HERE / "run_eval.py"), "--scenario", scenario, "--condition", condition,
             "--model", args.model, "--rep", str(rep), "--work", str(args.work), "--cache", str(args.cache),
             "--out", str(args.out)],
            capture_output=True, text=True,
        )
        status = "ok" if proc.returncode == 0 else f"FAILED ({proc.returncode})"
        print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {status} {scenario} {condition} rep {rep}", flush=True)
        if proc.returncode != 0:
            print(proc.stderr[-3000:], flush=True)
    print("MATRIX-DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
