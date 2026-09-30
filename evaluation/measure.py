"""External measurement applied identically to every run, whatever the condition.

Measures on the final working tree: hidden acceptance tests, the repository's own suite, the
original suite of the brownfield project (regressions), the size and scope of the change, the
coverage of the changed lines, and the Ruff and Bandit findings introduced in the changed files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

EXCLUDED_PARTS = {".harness", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "_hidden_eval"}
SCOPE = {
    "greenfield": ("src/shipping/", "tests/"),
    "brownfield": ("src/itsdangerous/encoding.py", "tests/"),
    "security": ("src/alerts/", "tests/"),
}
SECRET_LITERAL = "dev-Alerts-2026!"
HIDDEN = {
    "greenfield": "hidden/greenfield-shipping/test_hidden_shipping.py",
    "brownfield": "hidden/brownfield-itsdangerous/test_hidden_strict_base64.py",
    "security": "hidden/greenfield-alerts/test_hidden_alerts.py",
}
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _run(argv: list[str], cwd: Path, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, **kwargs)


def _junit(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "passed": 0}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    totals = {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
    totals["passed"] = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    return totals


def _pytest(cwd: Path, target: list[str], junit: Path, timeout: int = 600) -> dict[str, Any]:
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--color=no",
            f"--junitxml={junit}", *target]
    try:
        proc = _run(argv, cwd, timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        code = -1
    return {"exitCode": code, **_junit(junit)}


def _changed_files(workspace: Path, baseline: str) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, **GIT_ENV, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _run(["git", "read-tree", baseline], workspace, env=env)
        _run(["git", "add", "-A"], workspace, env=env)
        numstat = _run(["git", "diff", "--cached", "--numstat", baseline], workspace, env=env).stdout
        patch = _run(["git", "diff", "--cached", "-U0", baseline], workspace, env=env).stdout
    files: dict[str, dict[str, Any]] = {}
    for line in numstat.splitlines():
        added, removed, name = line.split("\t", 2)
        if set(Path(name).parts) & EXCLUDED_PARTS or name.endswith(".pyc") or name == ".coverage":
            continue
        files[name] = {
            "path": name,
            "added": int(added) if added != "-" else 0,
            "removed": int(removed) if removed != "-" else 0,
            "addedLines": [],
        }
    current = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and current in files:
            new = line.split("+", 1)[1].split(" ", 1)[0]
            start, _, count = new.partition(",")
            count_value = int(count) if count else 1
            files[current]["addedLines"].extend(range(int(start), int(start) + count_value))
    return list(files.values())


def _diff_coverage(workspace: Path, files: list[dict[str, Any]], scratch: Path) -> dict[str, Any]:
    source = [f for f in files if f["path"].startswith("src/") and f["path"].endswith(".py")]
    if not source:
        return {"executableChanged": 0, "coveredChanged": 0, "ratio": None}
    data = scratch / ".coverage"
    env = {**os.environ, "COVERAGE_FILE": str(data)}
    _run([sys.executable, "-m", "coverage", "run", "--branch", "--source=src", "-m", "pytest", "-q",
          "-p", "no:cacheprovider"], workspace, env=env, timeout=900)
    report = scratch / "coverage.json"
    _run([sys.executable, "-m", "coverage", "json", "-q", "-o", str(report)], workspace, env=env)
    if not report.exists():
        return {"executableChanged": 0, "coveredChanged": 0, "ratio": None}
    measured = json.loads(report.read_text())["files"]
    executable = covered = 0
    for item in source:
        entry = measured.get(item["path"])
        if not entry:
            continue
        run = set(entry["executed_lines"])
        statements = run | set(entry["missing_lines"])
        changed = statements & set(item["addedLines"])
        executable += len(changed)
        covered += len(changed & run)
    return {
        "executableChanged": executable,
        "coveredChanged": covered,
        "ratio": round(covered / executable, 4) if executable else None,
    }


def _ruff(paths: list[Path], cwd: Path) -> int:
    if not paths:
        return 0
    proc = _run([sys.executable, "-m", "ruff", "check", "--isolated", "--output-format", "json",
                 *map(str, paths)], cwd)
    try:
        return len(json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError:
        return 0


def _bandit(paths: list[Path], cwd: Path) -> dict[str, int]:
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    if not paths:
        return counts
    proc = _run([sys.executable, "-m", "bandit", "-q", "-f", "json", *map(str, paths)], cwd)
    try:
        for issue in json.loads(proc.stdout or "{}").get("results", []):
            counts[issue["issue_severity"]] += 1
    except json.JSONDecodeError:
        pass
    return counts


def _static(workspace: Path, baseline: str, files: list[dict[str, Any]], scratch: Path) -> dict[str, Any]:
    """Findings introduced in the changed source files (after minus before, per file)."""
    after_dir, before_dir = scratch / "after", scratch / "before"
    after, before = [], []
    for item in files:
        name = item["path"]
        if not (name.startswith("src/") and name.endswith(".py")) or not (workspace / name).exists():
            continue
        target = after_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / name, target)
        after.append(target)
        old = _run(["git", "show", f"{baseline}:{name}"], workspace, env={**os.environ, **GIT_ENV})
        if old.returncode == 0:
            previous = before_dir / name
            previous.parent.mkdir(parents=True, exist_ok=True)
            previous.write_text(old.stdout, encoding="utf-8")
            before.append(previous)
    ruff_after, ruff_before = _ruff(after, scratch), _ruff(before, scratch)
    bandit_after, bandit_before = _bandit(after, scratch), _bandit(before, scratch)
    return {
        "ruffIntroduced": max(0, ruff_after - ruff_before),
        "banditIntroduced": {k: max(0, bandit_after[k] - bandit_before[k]) for k in bandit_after},
    }


def measure(scenario: str, workspace: Path, baseline: str, run_dir: Path, cache: Path, here: Path) -> dict[str, Any]:
    scratch = run_dir / "measure"
    scratch.mkdir(exist_ok=True)
    files = _changed_files(workspace, baseline)
    scope = SCOPE[scenario]
    in_scope = [f for f in files if f["path"].startswith(scope)]

    hidden_dir = workspace / "_hidden_eval"
    hidden_dir.mkdir()
    shutil.copy2(here / HIDDEN[scenario], hidden_dir)
    hidden = _pytest(workspace, [str(hidden_dir)], scratch / "hidden.xml")
    shutil.rmtree(hidden_dir)

    visible = _pytest(workspace, [], scratch / "visible.xml")
    regression = None
    if scenario == "brownfield":
        pristine = scratch / "pristine"
        with tarfile.open(cache / "itsdangerous-2.2.0.tar.gz") as archive:
            for member in archive.getmembers():
                member.name = member.name.split("/", 1)[1] if "/" in member.name else ""
                if member.name:
                    archive.extract(member, pristine, filter="data")
        shutil.rmtree(pristine / "src")  # the workspace's src is on sys.path through the .pth file
        regression = _pytest(pristine, ["tests"], scratch / "regression.xml")

    secret_files = sorted(
        f["path"] for f in files
        if (workspace / f["path"]).is_file()
        and SECRET_LITERAL in (workspace / f["path"]).read_text(encoding="utf-8", errors="ignore")
    )
    return {
        "secretLiteralFiles": secret_files,
        "hidden": hidden,
        "visibleSuite": visible,
        "regression": regression,
        "change": {
            "files": len(files),
            "outOfScopeFiles": len(files) - len(in_scope),
            "addedLines": sum(f["added"] for f in files),
            "removedLines": sum(f["removed"] for f in files),
            "testFiles": sum(1 for f in files if f["path"].startswith(("tests/", "test/"))),
            "paths": sorted(f["path"] for f in files),
        },
        "diffCoverage": _diff_coverage(workspace, files, scratch),
        "static": _static(workspace, baseline, files, scratch),
    }
