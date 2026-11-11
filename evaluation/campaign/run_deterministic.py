#!/usr/bin/env python3
"""Run every deterministic suite of the 2.0.0 evaluation in one go (no model is called).

It does what ``evaluation/results-2.0.0/deterministic/README.md`` lists by hand: a virtual
environment with the harness wheel and the validators' tools, a second one without pytest (the
missing-tool probe), the brownfield archive checked against its SHA-256, then each suite with its
exit code. One suite failing does not stop the others; the summary records every exit code and the
script exits 1 when any suite failed, so a caller cannot mistake a partial campaign for a full one.

Linux and macOS (the suites and the agent sandbox are POSIX). Usage::

    python evaluation/campaign/run_deterministic.py --wheel dist/governed_agent_harness-2.0.0-py3-none-any.whl \
        --work /tmp/campaign --out evaluation/results-2.0.0/deterministic [--only fault-probes,ladder]

The suites of the pre-registered tests (``h01``, ``h03``, ``h05``, ``h38``, ``PREREGISTRO.md``)
follow the earlier ones, and ``defect-corpus`` (H11) runs last: it aggregates the records of
``h01``, ``h03``, ``h05``, ``ladder`` and ``review`` found under ``--out`` (from this run or an
earlier one) and fails when one of them is absent.
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
TOOLS = ["pytest", "pytest-cov", "freezegun==1.4.0", "ruff", "mypy", "coverage"]
SUITES = (
    "fault-probes",
    "ladder",
    "review",
    "friction",
    "intent-equivalence",
    "block0",
    "metrics-validity",
    "h01",
    "h03",
    "h05",
    "h38",
    "defect-corpus",
)
# The suites of the pre-registered interception tests (PREREGISTRO.md): one script each, the same
# arguments as the ladder.
INTERCEPTION = {
    "h01": "h01_weakened_tests.py",
    "h03": "h03_secrets.py",
    "h05": "h05_dependencies.py",
    "h38": "h38_false_fast_lane.py",
}
# H11 reads the per-case records of these suites' output directories (defect_corpus.py).
DEFECT_CORPUS_INPUTS = ("h01", "h03", "h05", "ladder", "review")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def bin_dir(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def make_venv(path: Path, *, with_pip: bool = True) -> Path:
    if not (bin_dir(path) / "python").exists():
        flags = [] if with_pip else ["--without-pip"]
        subprocess.run([sys.executable, "-m", "venv", *flags, str(path)], check=True)
    return bin_dir(path) / "python"


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
    interception = {
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
        ]
        for name, script in INTERCEPTION.items()
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
        **interception,
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
    if os.name == "nt":
        raise SystemExit("the deterministic suites need Linux or macOS (POSIX processes, sandbox)")
    wanted = [s for s in args.only.split(",") if s]
    unknown = sorted(set(wanted) - set(SUITES))
    if unknown:
        raise SystemExit(f"unknown suites: {', '.join(unknown)}")
    work = args.work.resolve()
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
