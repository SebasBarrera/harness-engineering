#!/usr/bin/env python3
"""H24: excessive context in large repositories, ``CATALOGO.md`` §2.4 and ``PREREGISTRO.md``
(third freeze). No model is called.

The threshold-discount task on five repositories: the sample project, and the generator of N16
(``block0_technical.large_project``: text files of about 11.7 kB under ``data/``) with 1,000 and
10,000 files in all, each in two contents: *neutral* (N16's text, which never names the task) and
*relevant* (every data file also names the task's key terms and its requirement id, so every
file is a candidate and the limits are exercised). ``build/`` and the ``.env`` of N16 are left
out (they do not reach the manifest).

Two conditions: ``H-manifest`` (``harness init``: ``context.manifest: auto``, ``maxFiles`` 40,
``maxBytes`` 400,000) and ``H-off`` (``context.manifest: off``). The fixture provider
(``budget_agent.py``) records, for every ``implement`` call it receives, what ``contextFiles``
carried: the number of files, the sum of their ``sizeBytes``, ``totalBytes``, candidates and
omitted, the bytes of the key and of the whole request.

Usage:
  python evaluation/deterministic/h24_context_bounds.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only sample,...]
      [--conditions H-manifest,H-off] [--reps 1] [--summarize-only] [--keep-work]

Writes ``DIR/h24-context-bounds.jsonl``, ``DIR/h24-context-bounds-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import git  # noqa: E402
from resource_support import (  # noqa: E402
    cleanup,
    correct_spec,
    discount_task,
    governed,
    main,
    proportion,
    read_log,
    run_state,
    run_task,
    saved_config,
    stamp_dir,
)
from suite_support import events  # noqa: E402

MAX_FILES = 40
MAX_BYTES = 400_000
LINE = "The quick brown fox jumps over the lazy dog; lorem ipsum dolor sit amet. "  # N16's line
RELEVANT = "threshold discount apply_discount subtotal variant req_1 ac_threshold\n"

CASES: list[dict[str, Any]] = [
    {"id": "sample", "files": 0, "content": "none"},
    {"id": "n1k-neutral", "files": 1_000, "content": "neutral"},
    {"id": "n1k-relevant", "files": 1_000, "content": "relevant"},
    {"id": "n10k-neutral", "files": 10_000, "content": "neutral"},
    {"id": "n10k-relevant", "files": 10_000, "content": "relevant"},
]


def conditions_of(case: dict[str, Any]) -> list[str]:
    del case
    return ["H-manifest", "H-off"]


def manifest_off(config: dict[str, Any]) -> None:
    config.setdefault("context", {})["manifest"] = "off"


def grow(ws: Path, case: dict[str, Any]) -> dict[str, Any]:
    """Add N16's data files until the repository has ``case['files']`` tracked files."""
    tracked = [line for line in git(ws, "ls-files").splitlines() if line]
    target = int(case["files"])
    count = max(0, target - len(tracked))
    for index in range(count):
        directory = ws / "data" / f"d{index // 100:03d}"
        directory.mkdir(parents=True, exist_ok=True)
        body = "".join(f"{index:05d}-{n:03d} {LINE}\n" for n in range(146))  # about 11.7 kB
        if case["content"] == "relevant":
            body += RELEVANT
        (directory / f"f{index:05d}.txt").write_bytes(body.encode("utf-8"))
    if count:
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "large repository data")
    files = [line for line in git(ws, "ls-files").splitlines() if line]
    size = sum((ws / f).stat().st_size for f in files if (ws / f).is_file())
    return {"trackedFiles": len(files), "trackedBytes": size, "dataFiles": count}


def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    del rep
    run_dir = stamp_dir(args, "h24", case["id"], condition)
    workspace = governed(
        run_dir, args, correct_spec(), mutate=manifest_off if condition == "H-off" else None
    )
    saved_config(args, condition, workspace)
    repository = grow(workspace.ws, case)
    started = run_task(workspace, discount_task(f"task_h24_{case['id'].replace('-', '_')}"))
    run_id = started["runId"]
    calls = [c for c in read_log(workspace.log) if c.get("kind") == "implement"]
    built = [
        e["payload"]
        for e in events(workspace.database, run_id)
        if e["type"] == "context.manifest.built"
    ]
    seconds = next((s["seconds"] for s in workspace.h.steps if s["step"] == "run start"), None)
    implement = [
        {
            "number": c.get("number"),
            "requestBytes": c.get("requestBytes"),
            "contextFiles": c.get("contextFiles"),
        }
        for c in calls
    ]
    within = [
        bool(
            (c["contextFiles"] or {}).get("present")
            and c["contextFiles"]["files"] <= MAX_FILES
            and c["contextFiles"]["sumSizeBytes"] <= MAX_BYTES
        )
        for c in implement
    ]
    record = {
        "repository": repository,
        "runId": run_id,
        "runExitCode": started["runExitCode"],
        "runStartSeconds": seconds,
        "implementCalls": implement,
        "manifestEvents": built,
        "allImplementWithinLimits": bool(implement) and all(within),
        "context": workspace.config.get("context"),
        "deviations": workspace.deviations,
        **{k: v for k, v in run_state(workspace, run_id).items() if k in ("runStatus", "phase")},
    }
    files = [(c["contextFiles"] or {}).get("files", 0) for c in implement]
    record["brief"] = f"{len(implement)} implement, context files {files}"
    cleanup(run_dir, args.keep_work)
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    rows = []
    for r in ok:
        for call in r.get("implementCalls") or []:
            context = call.get("contextFiles") or {}
            rows.append(
                {
                    "case": r["case"],
                    "condition": r["condition"],
                    "trackedFiles": r["repository"]["trackedFiles"],
                    "trackedBytes": r["repository"]["trackedBytes"],
                    "call": call.get("number"),
                    "present": bool(context.get("present")),
                    "files": context.get("files"),
                    "sumSizeBytes": context.get("sumSizeBytes"),
                    "totalBytes": context.get("totalBytes"),
                    "candidates": context.get("candidates"),
                    "omitted": context.get("omitted"),
                    "serializedBytes": context.get("serializedBytes"),
                    "requestBytes": call.get("requestBytes"),
                }
            )
    manifest = [r for r in ok if r["condition"] == "H-manifest"]
    within = sum(1 for r in manifest if r["allImplementWithinLimits"])
    summary = {
        "suite": "H24",
        "title": "excessive context in large repositories",
        "byRepository": {
            r["case"]: {
                "condition": r["condition"],
                "repository": r["repository"],
                "runStartSeconds": r.get("runStartSeconds"),
                "runStatus": r.get("runStatus"),
                "phase": r.get("phase"),
                "implementCalls": len(r.get("implementCalls") or []),
                "manifestEvents": r.get("manifestEvents"),
            }
            for r in ok
            if r["condition"] == "H-manifest"
        },
        "byRepositoryOff": {
            r["case"]: {
                "repository": r["repository"],
                "runStartSeconds": r.get("runStartSeconds"),
                "runStatus": r.get("runStatus"),
                "phase": r.get("phase"),
                "implementCalls": len(r.get("implementCalls") or []),
            }
            for r in ok
            if r["condition"] == "H-off"
        },
        "implementCalls": rows,
        "manifestCasesWithinLimits": proportion(within, len(manifest)),
        "offCallsWithContextFiles": sum(
            1 for row in rows if row["condition"] == "H-off" and row["present"]
        ),
    }
    summary["criteria"] = {
        "withinMaxFilesAndMaxBytes": {
            "value": f"{within}/{len(CASES)} repositories with every implement call within "
            f"{MAX_FILES} files and {MAX_BYTES} bytes ({len(manifest)} run)",
            "met": len(manifest) == len(CASES) and within == len(CASES),
        },
        "note": "Pre-registered in PREREGISTRO.md (H24); evaluated as is. A repository without an "
        "implement call carrying contextFiles counts against the criterion.",
    }
    return summary


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H24",
            title="excessive context in large repositories",
            stem="h24-context-bounds",
            cases=CASES,
            conditions_of=conditions_of,
            all_conditions=["H-manifest", "H-off"],
            runner=runner,
            summarize=summarize,
        )
    )
