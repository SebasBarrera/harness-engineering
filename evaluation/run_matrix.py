#!/usr/bin/env python3
"""Run the evaluation matrix for one model: scenarios x conditions x repetitions.

The order is counterbalanced per repetition with a fixed seed, so neither condition always runs
first. Each run is independent (fresh repository, fresh harness state); a failing run is logged
and the matrix continues. A run already in the output file (same scenario, condition, prompt and
repetition) is skipped, so a matrix that stopped resumes where it left off.

2.0.0 conditions: ``direct``, ``harness-core``, ``harness``, ``harness-tiered`` (``baseline`` is the
0.9.0 name of ``direct``). ``--dry-run`` and ``--provider`` are passed to every run (see run_eval.py).
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


def plan(
    model: str,
    reps: int,
    seed: int,
    scenarios: list[str],
    conditions: tuple[str, ...] = ("baseline", "harness"),
    first_rep: int = 1,
) -> list[tuple[str, str, int]]:
    rng = random.Random(f"{seed}:{model}")
    order: list[tuple[str, str, int]] = []
    for rep in range(1, first_rep + reps):
        cells = [(s, c, rep) for s in scenarios for c in conditions]
        rng.shuffle(cells)
        if rep >= first_rep:
            order += cells
    return order


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--first-rep", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--scenarios", default="greenfield,brownfield")
    parser.add_argument("--agent", choices=["claude", "codex"], default="claude")
    parser.add_argument("--prompt", choices=["full", "poor", "casual"], default="full")
    parser.add_argument("--conditions", default="baseline,harness")
    parser.add_argument("--effort", default="")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--provider", choices=["claude", "simulated"], default="claude")
    parser.add_argument("--core-reference-src", type=Path, default=None)
    args = parser.parse_args()
    done = set()
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            record = json.loads(line)
            condition = "direct" if record["condition"] == "baseline" else record["condition"]
            done.add((record["scenario"], condition, record.get("prompt", "full"), record["rep"]))
    order = plan(
        args.model,
        args.reps,
        args.seed,
        args.scenarios.split(","),
        tuple(args.conditions.split(",")),
        args.first_rep,
    )
    for scenario, condition, rep in order:
        key = (scenario, "direct" if condition == "baseline" else condition, args.prompt, rep)
        if key in done:
            continue
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        print(f"{stamp} start {scenario} {condition} {args.prompt} rep {rep}", flush=True)
        command = [
            sys.executable,
            str(HERE / "run_eval.py"),
            "--scenario",
            scenario,
            "--condition",
            condition,
            "--model",
            args.model,
            "--rep",
            str(rep),
            "--work",
            str(args.work),
            "--cache",
            str(args.cache),
            "--out",
            str(args.out),
            "--agent",
            args.agent,
            "--effort",
            args.effort,
            "--prompt",
            args.prompt,
            "--provider",
            args.provider,
        ]
        if args.dry_run:
            command.append("--dry-run")
        if args.core_reference_src:
            command += ["--core-reference-src", str(args.core_reference_src)]
        proc = subprocess.run(command, capture_output=True, text=True)
        status = "ok" if proc.returncode == 0 else f"FAILED ({proc.returncode})"
        print(
            f"{datetime.now(UTC).isoformat(timespec='seconds')} {status} {scenario} {condition} rep {rep}",
            flush=True,
        )
        if proc.returncode != 0:
            print(proc.stderr[-3000:], flush=True)
    print("MATRIX-DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
