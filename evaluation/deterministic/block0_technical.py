#!/usr/bin/env python3
"""Block 0 of the 2.0.0 evaluation: technical verification (P03, P04, P06, P07, N16). No model.

Sections (``--only`` selects some; each writes its own JSON next to the summary):

* ``tests``: the whole suite once with ``pytest --cov --cov-branch`` (JUnit and coverage JSON);
  tests and outcomes per family (unit, contract, integration, security, e2e, performance), line
  and branch coverage and statements of ``governed_harness``.
* ``security``: the tests of ``tests/security`` (P06 probes) with their outcomes, from the same
  JUnit report.
* ``static``: ``ruff check src tests scripts``, ``ruff format --check src tests scripts`` and
  ``mypy`` (strict, package mode), exit codes and finding counts.
* ``demo``: ``scripts/demo_flows.py all`` with a transcript: flows, steps, steps whose exit code
  differs from the expectation, and the output checks (``[ok ]``/``[BAD]``).
* ``benchmark``: ``harness benchmark run`` N times (default 5) and ``harness benchmark scenarios``.
* ``large``: the large-repository measurement documented in docs/reference/configuration.md
  (10,001 tracked files: 9,996 text files of about 11.7 kB plus the five files of the sample
  project, 500 ignored build files and an ignored ``.env``; one patch task of two files, the
  simulated provider, ``python.pytest`` only), with and without the three workspace keys
  (``snapshot``, ``baseline``, ``snapshotCache``), N repetitions: seconds of ``run start``,
  maximum resident set (``/usr/bin/time -l``), bytes stored and whether the ``.env`` secret was
  stored.

Usage:
  python evaluation/deterministic/block0_technical.py --repo REPO --python PY --out DIR
      [--harness PATH] [--wheel WHEEL] [--work DIR] [--only tests,static,...] [--bench-runs 5]
      [--large-reps 3] [--project-python PY]

``--python`` runs pytest, ruff, mypy and demo_flows (it must import the harness and the dev
tools); ``--repo`` is the source checkout whose ``tests/`` and ``scripts/`` are used (for the
wheel: a checkout of the tag ``v2.0.0``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    Harness,
    add_common_arguments,
    environment,
    init_repo,
    isolated_env,
    now,
    write_json,
)  # noqa: E402

FAMILIES = ("unit", "contract", "integration", "security", "e2e", "performance")
SECTIONS = ("tests", "security", "static", "demo", "benchmark", "large")


def env_for(args: argparse.Namespace) -> dict[str, str]:
    harness_bin = str(Path(args.harness).resolve().parent)
    python_bin = str(Path(args.python).resolve().parent)
    return {
        **os.environ,
        "PATH": f"{harness_bin}:{python_bin}:{os.environ.get('PATH', '')}",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def run(
    cmd: list[str], cwd: Path, env: dict[str, str], timeout: float | None = None
) -> tuple[int, str, str, float]:
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr, round(time.monotonic() - started, 2)


# ----- tests and coverage ------------------------------------------------------------------------
def section_tests(args: argparse.Namespace, out: Path) -> dict[str, Any]:
    junit = out / "junit.xml"
    coverage = out / "coverage.json"
    cmd = [
        args.python,
        "-m",
        "pytest",
        "-W",
        "error::ResourceWarning",
        "-p",
        "no:cacheprovider",
        "--cov=governed_harness",
        "--cov-branch",
        f"--cov-report=json:{coverage}",
        "--cov-report=",
        f"--junitxml={junit}",
    ]
    code, stdout, stderr, seconds = run(cmd, args.repo, env_for(args))
    (out / "pytest-output.txt").write_text(stdout[-20000:] + "\n" + stderr[-5000:])
    families: dict[str, dict[str, int]] = {
        f: {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0} for f in FAMILIES
    }
    security: list[dict[str, str]] = []
    if junit.exists():
        for case in ET.parse(junit).getroot().iter("testcase"):
            classname = case.get("classname", "")
            parts = classname.split(".")
            family = parts[1] if len(parts) > 1 and parts[0] == "tests" else "other"
            bucket = families.setdefault(
                family, {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
            )
            bucket["tests"] += 1
            outcome = "passed"
            if case.find("failure") is not None:
                outcome = "failed"
            elif case.find("error") is not None:
                outcome = "errors"
            elif case.find("skipped") is not None:
                outcome = "skipped"
            bucket[outcome] += 1
            if family == "security":
                security.append({"test": f"{classname}::{case.get('name')}", "outcome": outcome})
    totals = {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for bucket in families.values():
        for key in totals:
            totals[key] += bucket[key]
    cov: dict[str, Any] = {}
    if coverage.exists():
        data = json.loads(coverage.read_text())["totals"]
        cov = {
            "statements": data["num_statements"],
            "coveredLines": data["covered_lines"],
            "linePercent": round(100 * data["covered_lines"] / data["num_statements"], 2),
            "branches": data["num_branches"],
            "coveredBranches": data["covered_branches"],
            "branchPercent": round(100 * data["covered_branches"] / data["num_branches"], 2),
            "combinedPercent": round(data["percent_covered"], 2),
        }
        coverage.unlink()  # large; the totals are kept
    tail = [line for line in stdout.splitlines() if re.search(r"\d+ (passed|failed)", line)]
    return {
        "command": " ".join(
            cmd[:1]
            + [
                "-m",
                "pytest",
                "-W",
                "error::ResourceWarning",
                "--cov=governed_harness",
                "--cov-branch",
            ]
        ),
        "exitCode": code,
        "seconds": seconds,
        "pytestSummary": tail[-1] if tail else "",
        "byFamily": families,
        "totals": totals,
        "coverage": cov,
        "securityTests": security,
    }


# ----- static analysis ---------------------------------------------------------------------------
def section_static(args: argparse.Namespace) -> dict[str, Any]:
    env = env_for(args)
    ruff = [args.ruff] if args.ruff else [args.python, "-m", "ruff"]
    results: dict[str, Any] = {}
    code, stdout, stderr, _ = run(
        [*ruff, "check", "--output-format", "json", "src", "tests", "scripts"], args.repo, env
    )
    try:
        findings = len(json.loads(stdout))
    except json.JSONDecodeError:
        findings = None
    results["ruffCheck"] = {"exitCode": code, "findings": findings, "stderr": stderr[-500:]}
    code, stdout, stderr, _ = run(
        [*ruff, "format", "--check", "src", "tests", "scripts"], args.repo, env
    )
    results["ruffFormat"] = {
        "exitCode": code,
        "wouldReformat": len(re.findall(r"^Would reformat", stdout, re.M)),
        "summary": (stdout.strip().splitlines() or [""])[-1],
    }
    code, stdout, stderr, seconds = run([args.python, "-m", "mypy"], args.repo, env)
    results["mypy"] = {
        "exitCode": code,
        "summary": (stdout.strip().splitlines() or [""])[-1],
        "seconds": seconds,
    }
    results["versions"] = {
        "ruff": run([*ruff, "--version"], args.repo, env)[1].strip(),
        "mypy": run([args.python, "-m", "mypy", "--version"], args.repo, env)[1].strip(),
    }
    return results


# ----- demonstration flows -----------------------------------------------------------------------
def section_demo(args: argparse.Namespace, out: Path) -> dict[str, Any]:
    workdir = args.work / "demo"
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    transcript = out / "demo-transcript.json"
    env = env_for(args)
    env["HARNESS_STATE_DIR"] = str(args.work / "demo-state")
    env["HARNESS_ANCHOR_DIR"] = str(args.work / "demo-anchors")
    cmd = [
        args.python,
        "scripts/demo_flows.py",
        "all",
        "--workdir",
        str(workdir),
        "--transcript",
        str(transcript),
        "--harness",
        args.harness,
    ]
    code, stdout, stderr, seconds = run(cmd, args.repo, env)
    (out / "demo-output.txt").write_text(stdout[-60000:] + "\n" + stderr[-5000:])
    steps = json.loads(transcript.read_text()) if transcript.exists() else []
    if isinstance(steps, dict):
        steps = steps.get("steps", [])
    flows: dict[str, dict[str, int]] = {}
    for item in steps:
        flow = item.get("flow", "?")
        bucket = flows.setdefault(flow, {"steps": 0, "mismatches": 0})
        bucket["steps"] += 1
        if item.get("expected") != item.get("actual", item.get("exitCode")):
            bucket["mismatches"] += 1
    checks_ok = len(re.findall(r"^\[ok \] \S+\s+check:", stdout, re.M))
    checks_bad = len(re.findall(r"^\[BAD\] \S+\s+check:", stdout, re.M))
    return {
        "command": "python scripts/demo_flows.py all --workdir DIR --transcript FILE",
        "exitCode": code,
        "seconds": seconds,
        "flows": len(flows),
        "steps": sum(b["steps"] for b in flows.values()),
        "mismatches": sum(b["mismatches"] for b in flows.values()),
        "checksOk": checks_ok,
        "checksBad": checks_bad,
        "byFlow": flows,
        "workdir": str(workdir),
        "stateDir": env["HARNESS_STATE_DIR"],
    }


# ----- benchmarks --------------------------------------------------------------------------------
def section_benchmark(args: argparse.Namespace, out: Path) -> dict[str, Any]:
    env = env_for(args)
    micro = []
    for index in range(1, args.bench_runs + 1):
        target = out / f"benchmark-micro-{index}.json"
        code, _, stderr, seconds = run(
            [args.harness, "benchmark", "run", "--output", str(target)], args.work, env
        )
        micro.append(
            {
                "run": index,
                "exitCode": code,
                "seconds": seconds,
                "file": target.name,
                "stderr": stderr[-300:],
            }
        )
    target = out / "benchmark-scenarios.json"
    code, _, stderr, seconds = run(
        [args.harness, "benchmark", "scenarios", "--output", str(target)], args.work, env
    )
    return {
        "micro": micro,
        "scenarios": {
            "exitCode": code,
            "seconds": seconds,
            "file": target.name,
            "stderr": stderr[-300:],
        },
    }


# ----- large repository --------------------------------------------------------------------------
LARGE_TASK = (
    "taskId: task_large\n"
    "title: Implement threshold discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: ac_threshold\n"
    "    text: A subtotal of 100 with a ten percent rate returns 90.\n"
    "implementation:\n"
    "  mode: patch\n"
    "  patches:\n"
    "    - path: src/sample/pricing.py\n"
    "      operation: replace\n"
    "      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
    "    - path: tests/test_pricing.py\n"
    "      operation: append\n"
    "      content: |\n"
    "\n"
    "        def test_ac_threshold_at_threshold() -> None:\n"
    "            assert apply_discount(100, 100, 0.1) == 90\n"
)
SECRET = "API_TOKEN=do-not-store-0123456789"


def large_project(root: Path) -> None:
    (root / "src" / "sample").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "sample" / "__init__.py").write_text("from .pricing import apply_discount\n")
    (root / "src" / "sample" / "pricing.py").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n    return subtotal\n"
    )
    (root / "tests" / "test_pricing.py").write_text(
        "from sample import apply_discount\n\n\ndef test_below_threshold() -> None:\n    assert apply_discount(99, 100, 0.1) == 99\n"
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
    )
    (root / ".gitignore").write_text(".harness/\n__pycache__/\n.pytest_cache/\nbuild/\n.env\n")
    line = "The quick brown fox jumps over the lazy dog; lorem ipsum dolor sit amet. "  # 74 chars
    for index in range(9996):
        directory = root / "data" / f"d{index // 100:03d}"
        directory.mkdir(parents=True, exist_ok=True)
        body = "".join(f"{index:05d}-{n:03d} {line}\n" for n in range(146))  # about 11.7 kB
        (directory / f"f{index:05d}.txt").write_text(body)
    for index in range(500):
        (root / "build").mkdir(exist_ok=True)
        (root / "build" / f"artifact{index:03d}.o").write_text("x" * 4096)
    (root / ".env").write_text(SECRET + "\n")
    init_repo(root)


def tree_bytes(path: Path) -> tuple[int, int, bool]:
    total, largest, secret = 0, 0, False
    if not path.exists():
        return 0, 0, False
    for item in path.rglob("*"):
        if item.is_file():
            size = item.stat().st_size
            total += size
            largest = max(largest, size)
            if not secret and size < 400_000_000:
                with item.open("rb") as handle:
                    secret = SECRET.encode() in handle.read()
    return total, largest, secret


def section_large(args: argparse.Namespace) -> dict[str, Any]:
    import yaml

    results = []
    for variant in ("without-workspace-keys", "init"):
        for rep in range(1, args.large_reps + 1):
            root = args.work / "large" / f"{variant}-r{rep}"
            shutil.rmtree(root.parent / root.name, ignore_errors=True)
            started = time.monotonic()
            large_project(root)
            generated = round(time.monotonic() - started, 1)
            state = args.work / "large" / f"{variant}-r{rep}-state"
            shutil.rmtree(state, ignore_errors=True)
            project_bin = Path(args.project_python).resolve().parent
            h = Harness(args.harness, isolated_env([project_bin], state))
            h.run(root, "init", "--path", ".", label="init")
            config_path = root / ".harness" / "project.yaml"
            config = yaml.safe_load(config_path.read_text())
            config["validators"] = ["python.pytest"]
            if variant == "without-workspace-keys":
                for key in ("snapshot", "baseline", "snapshotCache"):
                    config["workspace"].pop(key, None)
            config_path.write_text(yaml.safe_dump(config, sort_keys=False))
            (root.parent / f"task-{root.name}.yaml").write_text(LARGE_TASK)
            h.run(
                root,
                "task",
                "create",
                "--path",
                ".",
                "--file",
                str(root.parent / f"task-{root.name}.yaml"),
                label="task create",
            )
            timed = [
                "/usr/bin/time",
                "-l",
                args.harness,
                "run",
                "start",
                "--path",
                ".",
                "--task",
                "task_large",
            ]
            begin = time.monotonic()
            proc = subprocess.run(timed, cwd=root, env=h.env, capture_output=True, text=True)
            seconds = round(time.monotonic() - begin, 2)
            match = re.search(r"(\d+)\s+maximum resident set size", proc.stderr)
            harness_bytes, harness_largest, harness_secret = tree_bytes(root / ".harness")
            state_bytes, state_largest, state_secret = tree_bytes(state / "state")
            try:
                started_payload = json.loads(proc.stdout)
            except json.JSONDecodeError:
                started_payload = {}
            tracked = int(
                subprocess.run(
                    ["git", "ls-files"], cwd=root, capture_output=True, text=True, env=h.env
                ).stdout.count("\n")
            )
            results.append(
                {
                    "variant": variant,
                    "rep": rep,
                    "trackedFiles": tracked,
                    "generationSeconds": generated,
                    "runStartExitCode": proc.returncode,
                    "runStatus": started_payload.get("status"),
                    "runPhase": started_payload.get("currentPhase"),
                    "runStartSeconds": seconds,
                    "maxResidentSetBytes": int(match.group(1)) if match else None,
                    "harnessDirBytes": harness_bytes,
                    "stateDirBytes": state_bytes,
                    "largestStoredFileBytes": max(harness_largest, state_largest),
                    "envSecretStored": harness_secret or state_secret,
                }
            )
            print(
                "large",
                variant,
                rep,
                results[-1]["runStartSeconds"],
                results[-1]["maxResidentSetBytes"],
                flush=True,
            )
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(state, ignore_errors=True)
    return {
        "runs": results,
        "configuration": "harness init with validators [python.pytest]; 'without-workspace-keys' removes workspace.snapshot, baseline and snapshotCache",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--python", required=True, help="python with the harness and the dev tools")
    parser.add_argument(
        "--project-python", default="", help="python with pytest for the large-repository project"
    )
    parser.add_argument("--ruff", default="", help="ruff executable (default: python -m ruff)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--only", default=",".join(SECTIONS))
    parser.add_argument("--bench-runs", type=int, default=5)
    parser.add_argument("--large-reps", type=int, default=3)
    args = parser.parse_args()
    args.repo = args.repo.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    args.work = (args.work or args.out / ".work").resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    args.project_python = args.project_python or args.python
    summary_path = args.out / "block0-summary.json"
    summary: dict[str, Any] = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    summary["environment"] = environment(
        args.harness, args.wheel, {"suite": "block0", "repo": str(args.repo)}
    )
    for section in args.only.split(","):
        print(f"== {section} ({now()})", flush=True)
        if section == "tests":
            summary["tests"] = section_tests(args, args.out)
            summary["security"] = {
                "tests": summary["tests"].pop("securityTests"),
                "family": summary["tests"]["byFamily"].get("security"),
            }
        elif section == "security":
            continue  # reported from the tests section's JUnit report
        elif section == "static":
            summary["static"] = section_static(args)
        elif section == "demo":
            summary["demo"] = section_demo(args, args.out)
        elif section == "benchmark":
            summary["benchmark"] = section_benchmark(args, args.out)
        elif section == "large":
            summary["large"] = section_large(args)
        write_json(summary_path, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
