#!/usr/bin/env python3
"""Reproduce the demonstration flows of the thesis with the installed ``harness`` CLI.

Every flow runs in a fresh temporary Git repository with the deterministic ``simulated``
provider, and every command's exit code is checked against the documented expectation.
The script is used by CI in three ways:

* ``quickstart``: the README quickstart, step by step (docs-smoke workflow);
* ``all``: quickstart plus the later-change, broken-baseline and Node.js flows, leaving
  the projects in ``--workdir`` so ``scripts/metrics_report.py`` can read them;
* any single flow name, for local debugging.

A JSON transcript (command, expected and actual exit code) is written with ``--transcript``.
Exit code 0 means every step matched its expectation.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PY_TASK = """\
taskId: task_python_add_discount
title: Add a percentage discount rule
intent: Apply a percentage discount only when the subtotal meets the configured threshold.
requirements:
  - requirementId: req_discount
    text: Apply a percentage discount only when the subtotal meets the configured threshold.
    source: example
acceptanceCriteria:
  - criterionId: ac_threshold
    text: A subtotal below the threshold is unchanged.
  - criterionId: ac_discount
    text: A subtotal at or above the threshold is reduced by the configured percentage.
constraints:
  - Do not change the public function signature.
implementation:
  mode: patch
  patches:
    - path: src/sample/pricing.py
      operation: replace
      content: |
        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:
            return subtotal * (1 - rate) if subtotal >= threshold else subtotal
    - path: tests/test_pricing.py
      operation: append
      content: |

        def test_at_threshold() -> None:
            assert apply_discount(100, 100, 0.1) == 90
"""

NODE_TASK = """\
taskId: task_node_config_precedence
title: Task configuration overrides repository configuration
intent: Resolve configuration with defaults, then repository values, then task values.
requirements:
  - requirementId: req_precedence
    text: Task-level values take precedence over repository values, which override defaults.
    source: example
acceptanceCriteria:
  - criterionId: ac_task_wins
    text: A task value overrides the repository value for the same key.
implementation:
  mode: patch
  patches:
    - path: src/resolve-config.js
      operation: replace
      content: |
        export function resolveConfig(defaults, repository, task) {
          return { ...defaults, ...repository, ...task };
        }
    - path: test/resolve-config.test.js
      operation: append
      content: |

        test('task overrides repository', () => {
          assert.deepEqual(resolveConfig({mode:'safe'}, {mode:'strict'}, {mode:'task'}), {mode:'task'});
        });
"""


class Transcript:
    def __init__(self, harness: str) -> None:
        self.harness = harness
        self.steps: list[dict[str, Any]] = []
        self.ok = True

    def run(
        self, flow: str, cwd: Path, args: Sequence[str], expect: int
    ) -> subprocess.CompletedProcess[str]:
        argv = [self.harness, *args]
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
        matched = proc.returncode == expect
        self.ok &= matched
        self.steps.append(
            {
                "flow": flow,
                "command": "harness " + " ".join(args),
                "expected": expect,
                "actual": proc.returncode,
                "ok": matched,
            }
        )
        mark = "ok " if matched else "BAD"
        print(
            f"[{mark}] {flow:<14} exit {proc.returncode} (expected {expect})  harness {' '.join(args)}"
        )
        if not matched:
            print(proc.stdout[-2000:], proc.stderr[-2000:], sep="\n", file=sys.stderr)
        return proc

    def json(self, flow: str, cwd: Path, args: Sequence[str], expect: int) -> Any:
        return json.loads(self.run(flow, cwd, args, expect).stdout)


def git(cwd: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Demo",
            "-c",
            "user.email=demo@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=env,
    )


def python_project(root: Path) -> Path:
    (root / "src" / "sample").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "sample" / "__init__.py").write_text("from .pricing import apply_discount\n")
    (root / "src" / "sample" / "pricing.py").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "    return subtotal\n"
    )
    (root / "tests" / "test_pricing.py").write_text(
        "from sample import apply_discount\n\n\n"
        "def test_below_threshold() -> None:\n"
        "    assert apply_discount(99, 100, 0.1) == 99\n"
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n\n'
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
    )
    (root / ".gitignore").write_text(".harness/\n__pycache__/\n.pytest_cache/\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")
    return root


def node_project(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "test").mkdir()
    (root / "src" / "resolve-config.js").write_text(
        "export function resolveConfig(defaults, repository, task) {\n"
        "  return { ...defaults, ...task, ...repository };\n}\n"
    )
    (root / "test" / "resolve-config.test.js").write_text(
        "import assert from 'node:assert/strict';\nimport test from 'node:test';\n"
        "import { resolveConfig } from '../src/resolve-config.js';\n\n"
        "test('repository overrides defaults', () => {\n"
        "  assert.deepEqual(resolveConfig({mode:'safe'}, {mode:'strict'}, {}), {mode:'strict'});\n});\n"
    )
    (root / "package.json").write_text(
        '{\n  "name": "node-sample",\n  "version": "0.1.0",\n  "private": true,\n'
        '  "type": "module",\n  "scripts": {\n    "test": "node --test"\n  }\n}\n'
    )
    (root / ".gitignore").write_text(".harness/\nnode_modules/\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")
    return root


def current_digest(t: Transcript, flow: str, root: Path, run_id: str) -> str:
    status = t.json(flow, root, ["status", "--path", ".", "--run", run_id], 0)
    return str(status["execution"]["changeSetDigest"])


def flow_quickstart(t: Transcript, root: Path) -> None:
    """README quickstart: happy path on a new Python project, ending with APPROVE."""
    flow = "quickstart"
    python_project(root)
    (root / "task.yaml").write_text(PY_TASK)
    t.run(flow, root, ["init", "--path", "."], 0)
    t.run(flow, root, ["inspect", "--path", "."], 0)
    t.run(flow, root, ["doctor", "--path", ".", "--json"], 0)
    t.run(flow, root, ["config", "validate", "--path", "."], 0)
    t.run(flow, root, ["task", "create", "--path", ".", "--file", "task.yaml"], 0)
    run = t.json(
        flow, root, ["run", "start", "--path", ".", "--task", "task_python_add_discount"], 4
    )
    run_id = run["executionId"]
    t.run(flow, root, ["findings", "list", "--path", ".", "--run", run_id], 0)
    t.run(flow, root, ["evidence", "list", "--path", ".", "--run", run_id], 0)
    digest = current_digest(t, flow, root, run_id)
    t.run(
        flow,
        root,
        [
            "gate",
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--decision",
            "APPROVE",
            "--change-set-digest",
            digest,
            "--actor",
            "human.reviewer",
            "--rationale",
            "Acceptance criteria and independent evidence reviewed",
        ],
        0,
    )
    for fmt in ("markdown", "json", "jsonl", "sarif"):
        t.run(
            flow,
            root,
            ["trace", "--path", ".", "--run", run_id, "--format", fmt, "--output", f"trace.{fmt}"],
            0,
        )
    t.run(flow, root, ["retrospect", "--path", ".", "--run", run_id], 0)


def flow_later_change(t: Transcript, root: Path) -> None:
    """Thesis flow 4: a change after a passing gate invalidates the pending approval."""
    flow = "later-change"
    python_project(root)
    (root / "task.yaml").write_text(PY_TASK)
    t.run(flow, root, ["init", "--path", "."], 0)
    t.run(flow, root, ["task", "create", "--path", ".", "--file", "task.yaml"], 0)
    run_id = t.json(
        flow, root, ["run", "start", "--path", ".", "--task", "task_python_add_discount"], 4
    )["executionId"]
    stale = current_digest(t, flow, root, run_id)
    with (root / "src" / "sample" / "pricing.py").open("a") as handle:
        handle.write("# manual edit after the gate was evaluated\n")
    t.run(
        flow,
        root,
        [
            "gate",
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--decision",
            "APPROVE",
            "--change-set-digest",
            stale,
            "--actor",
            "human.reviewer",
            "--rationale",
            "Stale approval attempt",
        ],
        5,
    )
    t.run(flow, root, ["run", "continue", "--path", ".", "--run", run_id], 4)
    status = t.json(flow, root, ["status", "--path", ".", "--run", run_id], 0)
    if status["gate"]["status"] != "INCONCLUSIVE":
        t.ok = False
        print(f"[BAD] {flow}: expected INCONCLUSIVE gate, got {status['gate']['status']}")
    digest = status["execution"]["changeSetDigest"]
    t.run(
        flow,
        root,
        [
            "gate",
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--decision",
            "REQUEST_CHANGES",
            "--change-set-digest",
            digest,
            "--actor",
            "human.reviewer",
            "--rationale",
            "Manual edit after review; verify again",
        ],
        0,
    )
    t.run(flow, root, ["run", "continue", "--path", ".", "--run", run_id], 4)
    digest = current_digest(t, flow, root, run_id)
    t.run(
        flow,
        root,
        [
            "gate",
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--decision",
            "APPROVE",
            "--change-set-digest",
            digest,
            "--actor",
            "human.reviewer",
            "--rationale",
            "Verified again after the correction",
        ],
        0,
    )


def flow_broken_baseline(t: Transcript, root: Path) -> None:
    """Brownfield-style flow (thesis flow 3): the baseline already has a failing test outside the
    ChangeSet. Verification fails; after the baseline is repaired the ChangeSet digest is
    unchanged, the earlier HIGH finding still counts (issue #2), APPROVE is refused with exit 5
    and the run closes only with a justified APPROVE_EXCEPTION."""
    flow = "broken-baseline"
    python_project(root)
    legacy = root / "tests" / "test_legacy.py"
    legacy.write_text("def test_legacy_behaviour() -> None:\n    assert 1 + 1 == 3\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "legacy test (already broken)")
    (root / "task.yaml").write_text(PY_TASK)
    t.run(flow, root, ["init", "--path", "."], 0)
    t.run(flow, root, ["task", "create", "--path", ".", "--file", "task.yaml"], 0)
    run_id = t.json(
        flow, root, ["run", "start", "--path", ".", "--task", "task_python_add_discount"], 6
    )["executionId"]
    t.run(flow, root, ["findings", "list", "--path", ".", "--run", run_id], 0)
    before = current_digest(t, flow, root, run_id)
    legacy.write_text("def test_legacy_behaviour() -> None:\n    assert 1 + 1 == 2\n")
    t.run(flow, root, ["run", "continue", "--path", ".", "--run", run_id], 4)
    status = t.json(flow, root, ["status", "--path", ".", "--run", run_id], 0)
    digest = status["execution"]["changeSetDigest"]
    if digest != before or status["gate"]["status"] != "FAILED":
        t.ok = False
        print(
            f"[BAD] {flow}: expected unchanged digest and FAILED gate, got {status['gate']['status']}"
        )
    t.run(
        flow,
        root,
        [
            "gate",
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--decision",
            "APPROVE",
            "--change-set-digest",
            digest,
            "--actor",
            "human.reviewer",
            "--rationale",
            "Plain approval over a failed gate",
        ],
        5,
    )
    t.run(
        flow,
        root,
        [
            "gate",
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--decision",
            "APPROVE_EXCEPTION",
            "--change-set-digest",
            digest,
            "--actor",
            "human.reviewer",
            "--rationale",
            "Failure came from a pre-existing test outside the ChangeSet; repaired and re-verified",
        ],
        0,
    )


def flow_node(t: Transcript, root: Path) -> None:
    """Thesis flow 2: Node.js project, gate PASSED, ordinary approval."""
    flow = "node"
    node_project(root)
    (root / "task.yaml").write_text(NODE_TASK)
    t.run(flow, root, ["init", "--path", "."], 0)
    t.run(flow, root, ["inspect", "--path", "."], 0)
    t.run(flow, root, ["task", "create", "--path", ".", "--file", "task.yaml"], 0)
    run_id = t.json(
        flow, root, ["run", "start", "--path", ".", "--task", "task_node_config_precedence"], 4
    )["executionId"]
    digest = current_digest(t, flow, root, run_id)
    t.run(
        flow,
        root,
        [
            "gate",
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--decision",
            "APPROVE",
            "--change-set-digest",
            digest,
            "--actor",
            "human.reviewer",
            "--rationale",
            "Precedence fixed and tested",
        ],
        0,
    )


FLOWS = {
    "quickstart": flow_quickstart,
    "later-change": flow_later_change,
    "broken-baseline": flow_broken_baseline,
    "node": flow_node,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("flow", choices=[*FLOWS, "all"])
    parser.add_argument(
        "--workdir", type=Path, help="keep projects here (default: temporary, deleted)"
    )
    parser.add_argument("--transcript", type=Path, help="write the JSON transcript here")
    parser.add_argument("--harness", default=shutil.which("harness") or "harness")
    args = parser.parse_args(argv)

    names = list(FLOWS) if args.flow == "all" else [args.flow]
    if "node" in names and shutil.which("npm") is None:
        raise SystemExit("npm is required for the Node.js flow")
    temp = None
    if args.workdir is None:
        temp = tempfile.mkdtemp(prefix="harness-demo-")
        workdir = Path(temp)
    else:
        workdir = args.workdir
        workdir.mkdir(parents=True, exist_ok=True)
    transcript = Transcript(args.harness)
    try:
        for name in names:
            target = workdir.resolve() / name
            if target.exists():
                shutil.rmtree(target)
            FLOWS[name](transcript, target)
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)
    if args.transcript:
        args.transcript.write_text(json.dumps(transcript.steps, indent=2) + "\n", encoding="utf-8")
    failed = [step for step in transcript.steps if not step["ok"]]
    print(f"{len(transcript.steps)} steps, {len(failed)} unexpected exit code(s)")
    return 0 if transcript.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
