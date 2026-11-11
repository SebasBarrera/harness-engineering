#!/usr/bin/env python3
"""Run every deterministic suite of the 2.0.0 evaluation in one go (no model is called).

It does what ``evaluation/results-2.0.0/deterministic/README.md`` lists by hand: a virtual
environment with the harness wheel and the validators' tools, a second one without pytest (the
missing-tool probe), the brownfield archive checked against its SHA-256, then each suite with its
exit code. One suite failing does not stop the others; the summary records every exit code and the
script exits 1 when any suite failed, so a caller cannot mistake a partial campaign for a full one.

Linux, macOS and Windows (H46, ``PREREGISTRO.md``). Windows has no agent sandbox, so the suites
run the governed conditions there with ``runtime.agentSandbox: off`` and record it in each
``environment.json``; its ``--work`` must be short (at most 40 characters: the suites' paths stay
under the 260-character limit). Usage::

    python evaluation/campaign/run_deterministic.py --wheel dist/governed_agent_harness-2.0.0-py3-none-any.whl \
        --work /tmp/campaign --out evaluation/results-2.0.0/deterministic [--only fault-probes,ladder]

The suites of the pre-registered tests (``h01``–``h07``, ``h09``, ``h10``, ``h12``–``h20``,
``h22``–``h24``, ``h27``, ``h28``, ``h31``, ``h32``, ``h35``, ``h36``, ``h38``, ``h40``, ``h41``,
``h44``, ``h45``, ``PREREGISTRO.md``) follow the earlier ones, and ``defect-corpus`` (H11) runs
last: it aggregates the records of ``h01``–``h07``, ``h09``, ``h10``, ``ladder`` and ``review``
found under ``--out`` (from this run or an earlier one) and fails when one of them is absent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent
REPO = EVAL_DIR.parent
ITSDANGEROUS_URL = "https://github.com/pallets/itsdangerous/archive/refs/tags/2.2.0.tar.gz"
ITSDANGEROUS_SHA256 = "7b0c6d4186e963b88489b69603b7ab2bf7c8e9eb4135a7b13b5f21bd4b937f2b"
# The validators' tools of the deterministic suites (evaluation/results-2.0.0/deterministic).
# Exact versions (amendment 2026-11-11 (2) of H44, PREREGISTRO.md): the ratchet and the validators
# see the same rule defaults on every campaign run; they match requirements/ and the launchers.
TOOLS = [
    "pytest==8.3.5",
    "pytest-cov==7.1.0",
    "freezegun==1.4.0",
    "ruff==0.17.4",
    "mypy==2.4.1",
    "coverage==7.17.2",
]
# The suites of the pre-registered tests (PREREGISTRO.md): one script each, the same arguments as
# the ladder (--out, --work, --project-venv), plus the extras of suite_commands where a suite needs
# more.
PREREGISTERED = {
    "h01": "h01_weakened_tests.py",
    "h02": "h02_lying_tests.py",
    "h03": "h03_secrets.py",
    "h04": "h04_scope_creep.py",
    "h05": "h05_dependencies.py",
    "h06": "h06_layer_violations.py",
    "h07": "h07_unsupported_claim.py",
    "h09": "h09_after_approval.py",
    "h10": "h10_uncovered_regressions.py",
    "h12": "h12_frozen_acceptance.py",
    "h13": "h13_bundle_audit.py",
    "h14": "event_tampering.py",
    "h15": "h15_traceability.py",
    "h16": "h16_decider_identity.py",
    "h17": "h17_retention.py",
    "h18": "iteration_cap.py",
    "h19": "h19_size_budgets.py",
    "h20": "h20_cost_ceilings.py",
    "h22": "context_bytes.py",
    "h23": "contract_violations.py",
    "h24": "h24_context_bounds.py",
    "h27": "h27_layer_templates.py",
    "h28": "h28_reverse_engineering.py",
    "h31": "h31_tdd_red_green.py",
    "h32": "h32_bdd_frozen.py",
    "h35": "h35_light_mutation.py",
    "h36": "h36_principles.py",
    "h38": "h38_false_fast_lane.py",
    "h40": "h40_embedded_mcp.py",
    "h41": "h41_routing_invariants.py",
    "h44": "h44_block_clarity.py",
    "h45": "h45_clean_changes.py",
    "h47": "h47_crash_resume.py",
    "h48": "h48_concurrent_runs.py",
    "h50": "h50_degraded_provider.py",
    "h51": "h51_doctor.py",
}
SUITES = (
    "fault-probes",
    "ladder",
    "review",
    "friction",
    "intent-equivalence",
    "block0",
    "metrics-validity",
    *PREREGISTERED,
    "defect-corpus",
)
# On Windows the work directory must leave the suites' deepest paths under 260 characters.
WINDOWS_MAX_WORK = 40
# H11 reads the per-case records of these suites' output directories (defect_corpus.py).
# H11's corpus is made of the defects of H01-H10 (PREREGISTRO.md) plus N01-a and N02-a.
DEFECT_CORPUS_INPUTS = (
    "h01",
    "h02",
    "h03",
    "h04",
    "h05",
    "h06",
    "h07",
    "h09",
    "h10",
    "ladder",
    "review",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def bin_dir(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def make_venv(path: Path, *, with_pip: bool = True) -> Path:
    python = bin_dir(path) / ("python.exe" if os.name == "nt" else "python")
    if not python.exists():
        flags = [] if with_pip else ["--without-pip"]
        subprocess.run([sys.executable, "-m", "venv", *flags, str(path)], check=True)
    return python


def cache_archive(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / "itsdangerous-2.2.0.tar.gz"
    if not archive.exists():
        urllib.request.urlretrieve(ITSDANGEROUS_URL, archive)  # noqa: S310 - fixed https URL
    found = sha256(archive)
    if found != ITSDANGEROUS_SHA256:
        archive.unlink()
        raise SystemExit(
            f"itsdangerous-2.2.0.tar.gz has SHA-256 {found}, expected {ITSDANGEROUS_SHA256}"
        )
    return archive


def suite_commands(
    py: Path, venv: Path, nopytest: Path, cache: Path, work: Path, out: Path, repo: Path
) -> dict[str, list[str]]:
    d = EVAL_DIR / "deterministic"
    ruff = bin_dir(venv) / "ruff"
    extra = {
        # H14's clock-shift kind runs timeshift.py with the harness's python; the fixture's call
        # log stays under the work directory.
        "h14": ["--harness-python", str(py), "--log-root", str(work / "h14-log")],
        # H22's brownfield task reads the itsdangerous archive.
        "h22": ["--cache", str(cache)],
    }
    preregistered = {
        name: [
            str(py),
            str(d / script),
            "--out",
            str(out / name),
            "--work",
            str(work / name),
            "--project-venv",
            str(venv),
            # H11 (PREREGISTRO.md) aggregates three repetitions of the defect suites.
            *(["--reps", "3"] if name in DEFECT_CORPUS_INPUTS else []),
            *extra.get(name, []),
        ]
        for name, script in PREREGISTERED.items()
    }
    return {
        "fault-probes": [
            str(py),
            str(EVAL_DIR / "fault_probes.py"),
            "--work",
            str(work / "probes"),
            "--cache",
            str(cache),
            "--out",
            str(out / "fault-probes" / "fault-probes.jsonl"),
            "--reps",
            "3",
            "--project-venv",
            str(venv),
            "--nopytest-venv",
            str(nopytest),
        ],
        "ladder": [
            str(py),
            str(d / "ladder_corpus.py"),
            "--out",
            str(out / "ladder"),
            "--work",
            str(work / "ladder"),
            "--project-venv",
            str(venv),
        ],
        "review": [
            str(py),
            str(d / "review_corpus.py"),
            "--out",
            str(out / "review"),
            "--work",
            str(work / "review"),
            "--project-venv",
            str(venv),
        ],
        "friction": [
            str(py),
            str(d / "friction.py"),
            "--out",
            str(out / "friction"),
            "--work",
            str(work / "friction"),
            "--project-venv",
            str(venv),
        ],
        "intent-equivalence": [
            str(py),
            str(d / "intent_equivalence.py"),
            "--out",
            str(out / "intent-equivalence"),
        ],
        "block0": [
            str(py),
            str(d / "block0_technical.py"),
            "--repo",
            str(repo),
            "--python",
            str(py),
            "--project-python",
            str(py),
            "--ruff",
            str(ruff),
            "--out",
            str(out / "block0"),
            "--work",
            str(work / "block0"),
        ],
        "metrics-validity": [
            str(py),
            str(d / "metrics_validity.py"),
            "--out",
            str(out / "metrics-validity"),
            "--runs-glob",
            str(work / "friction" / "friction-*"),
            "--demo-workdir",
            str(work / "block0" / "demo"),
            "--demo-state",
            str(work / "block0" / "demo-state"),
        ],
        **preregistered,
        # Runs last: it reads the results of the suites above and re-runs nothing.
        "defect-corpus": [
            str(py),
            str(d / "defect_corpus.py"),
            "--out",
            str(out / "defect-corpus"),
            "--inputs",
            *(str(out / name) for name in DEFECT_CORPUS_INPUTS),
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wheel", type=Path, required=True, help="the harness wheel under test")
    parser.add_argument("--work", type=Path, required=True, help="work directory outside the repo")
    parser.add_argument("--out", type=Path, required=True, help="results directory")
    parser.add_argument("--repo", type=Path, default=REPO, help="checkout block0 verifies")
    parser.add_argument("--only", default=",".join(SUITES))
    args = parser.parse_args(argv)
    wanted = [s for s in args.only.split(",") if s]
    unknown = sorted(set(wanted) - set(SUITES))
    if unknown:
        raise SystemExit(f"unknown suites: {', '.join(unknown)}")
    work = args.work.resolve()
    if os.name == "nt" and len(str(work)) > WINDOWS_MAX_WORK:
        raise SystemExit(
            f"--work {work} is longer than {WINDOWS_MAX_WORK} characters: on Windows the suites' "
            "paths would pass the 260-character limit"
        )
    out = args.out.resolve()
    wheel = args.wheel.resolve()
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    venv = work / "venv"
    py = make_venv(venv)
    subprocess.run([str(py), "-m", "pip", "install", "-q", str(wheel), *TOOLS], check=True)
    nopytest = work / "nopytest"
    make_venv(nopytest, with_pip=False)
    cache = work / "cache"
    cache_archive(cache)

    env = {
        **os.environ,
        "HARNESS_BIN": str(bin_dir(venv) / "harness"),
        "HARNESS_WHEEL": str(wheel),
        # H10's brownfield candidates come from the itsdangerous archive checked above.
        "EVAL_CACHE": str(cache),
        "PATH": f"{bin_dir(venv)}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    env.pop("PYTHONPATH", None)
    commands = suite_commands(py, venv, nopytest, cache, work, out, args.repo.resolve())
    results: list[dict[str, object]] = []
    for name in SUITES:
        if name not in wanted:
            continue
        (out / name).mkdir(parents=True, exist_ok=True)
        log = out / name / "campaign-log.txt"
        begin = time.monotonic()
        with log.open("w", encoding="utf-8") as handle:
            proc = subprocess.run(
                commands[name], cwd=REPO, env=env, stdout=handle, stderr=subprocess.STDOUT
            )
        seconds = round(time.monotonic() - begin, 1)
        results.append({"suite": name, "exitCode": proc.returncode, "seconds": seconds})
        print(f"{name}: exit {proc.returncode} in {seconds} s", flush=True)

    sanitizer = EVAL_DIR / "deterministic" / "sanitize_paths.py"
    # Local paths never reach the results (evaluation/README.md); a failure here must stop.
    subprocess.run([str(py), str(sanitizer), str(out), "--work", str(work)], cwd=REPO, check=True)
    summary = {
        "finishedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "wheel": wheel.name,
        "wheelSha256": sha256(wheel),
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "sandbox": shutil.which("bwrap") or shutil.which("sandbox-exec"),
        "suites": results,
        "failed": [r["suite"] for r in results if r["exitCode"] != 0],
    }
    if summary["sandbox"]:
        summary["sandbox"] = Path(str(summary["sandbox"])).name
    (out / "campaign-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
