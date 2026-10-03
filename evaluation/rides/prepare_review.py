#!/usr/bin/env python3
"""Build a blind review folder for one group of rides cells (see REVIEW.md).

Copies src/ and tests/ of each run's workspace (nothing else: no .harness, prompts or logs) as
implementations A, B, C in an order shuffled with a fixed seed, plus SPEC.md, INTERFACE.md and
REVIEW.md. The key (letter -> run directory) is written next to the folder, not inside it.

Usage: python prepare_review.py OUT_DIR RUN_DIR [RUN_DIR ...]
"""

from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    out, runs = Path(sys.argv[1]), [Path(a) for a in sys.argv[2:]]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    order = sorted(runs, key=lambda p: p.name)
    random.Random(out.name).shuffle(order)
    key = {}
    for letter, run in zip("ABCDEF", order, strict=False):
        target = out / letter
        target.mkdir()
        for part in ("src", "tests"):
            if (run / "ws" / part).is_dir():
                shutil.copytree(run / "ws" / part, target / part,
                                ignore=shutil.ignore_patterns("__pycache__", "*.db", "*.sqlite", ".pytest_cache"))
        key[letter] = run.name
    for name, source in (("SPEC.md", HERE / "SPEC.md"), ("INTERFACE.md", HERE / "fixture" / "INTERFACE.md"),
                         ("REVIEW.md", HERE / "REVIEW.md")):
        shutil.copy2(source, out / name)
    (out.parent / f"{out.name}.key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(json.dumps({"folder": str(out), "implementations": sorted(key)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
