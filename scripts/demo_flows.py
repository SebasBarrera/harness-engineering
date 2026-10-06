#!/usr/bin/env python3
"""Reproduce the demonstration flows of the thesis with the installed ``harness`` CLI.

Every flow runs in a fresh temporary Git repository with the deterministic ``simulated``
provider or a fixture command provider that calls no model, and every command's exit code is
checked against the documented expectation. The script is used by CI in three ways:

* ``quickstart``: the README quickstart, command for command (docs-smoke workflow);
* ``all``: quickstart plus the later-change, broken-baseline, review-exception, Node.js, memory,
  clarification, traceability, corrections, integrity, delivery, agent-results and ladder flows,
  the gitlab, tdd and bdd flows of wave 6, the review panel flows of wave 7 (a project rule,
  a cache hit, an UNKNOWN reviewer, the hook mode) and the friction flow of wave 8, leaving
  the projects in ``--workdir`` so ``scripts/metrics_report.py`` can read them (with ``HARNESS_STATE_DIR`` pointing at the same
  run registry: ``runtime.stateDir: auto``, written by init, keeps it outside the workspaces);
* any single flow name, for local debugging.

A JSON transcript (command, expected and actual exit code) is written with ``--transcript``.
Exit code 0 means every step matched its expectation.

The harness runs without the developer's global or system Git configuration (as the fixture
repositories do): a closure commit (``delivery.closureCommit``) would otherwise be signed with the
developer's key.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

GUIDE_TASK = Path(__file__).resolve().parent.parent / "docs" / "guides" / "task.yaml"

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

        def test_req_discount_at_threshold() -> None:
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

        test('req_precedence: task overrides repository', () => {
          assert.deepEqual(resolveConfig({mode:'safe'}, {mode:'strict'}, {mode:'task'}), {mode:'task'});
        });
"""


FOLLOW_UP_TASK = """\
taskId: {task_id}
title: Cover another discount case
intent: Add a regression test for {case}.
acceptanceCriteria:
  - criterionId: ac_case
    text: The new test passes with the current implementation.
implementation:
  mode: patch
  patches:
    - path: tests/test_pricing.py
      operation: append
      content: |

        def {test}() -> None:
            assert apply_discount({subtotal}, 100, 0.1) == {expected}
"""

USAGE_ADAPTER = """\
import json
import sys
from pathlib import Path

request = json.load(sys.stdin)
for patch in request["task"]["implementation"]["patches"]:
    with Path(patch["path"]).open("a", encoding="utf-8") as handle:
        handle.write(patch.get("content") or "")
# Fixed figures: this fixture exercises the usage protocol, it does not call a model.
usage = {"inputTokens": 1200, "outputTokens": 340, "costUsd": 0.0125}
print(json.dumps({"status": "PASSED", "summary": "Patches applied", "usage": usage}))
"""


class Transcript:
    def __init__(self, harness: str) -> None:
        self.harness = harness
        self.steps: list[dict[str, Any]] = []
        self.ok = True

    def run(
        self,
        flow: str,
        cwd: Path,
        args: Sequence[str],
        expect: int,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = [self.harness, *args]
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            env={**isolated_env(), **(env or {})},
        )
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

    def json(
        self,
        flow: str,
        cwd: Path,
        args: Sequence[str],
        expect: int,
        env: dict[str, str] | None = None,
    ) -> Any:
        return json.loads(self.run(flow, cwd, args, expect, env).stdout)

    def check(self, flow: str, condition: bool, description: str) -> None:
        """Record an assertion on the output of a step, next to its exit code."""
        self.ok &= condition
        mark = "ok " if condition else "BAD"
        print(f"[{mark}] {flow:<14} check: {description}")


def isolated_env() -> dict[str, str]:
    """The environment of every command: the current one (read when the command runs, so the
    anchor and state directories ``main`` sets reach the harness) without the developer's global
    or system Git configuration."""
    return {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git_output(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=isolated_env()
    ).stdout.strip()


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


AGENT_RESULTS_KEYS: dict[str, tuple[str, ...]] = {
    "intake": ("ambiguityReview", "clarifyAgent", "validateAnswers", "projectSetup"),
    "verification": (
        "interface",
        "architecture",
        "securityPatterns",
        "constraints",
        "ratchet",
        "invariants",
        "differential",
        "weakenedControls",
        "testQuality",
        "secrets",
        "sarif",
        "riskFactors",
        "acceptanceTests",
        "principles",
    ),
    "review": ("agentReview", "reviewer", "structuredChanges", "panel"),
    "runtime": ("gateContract", "reproduceFirst"),
    "governance": (
        "stopTheLine",
        "phasePermissions",
        "phaseCapabilities",
        "applyRepositoryPolicies",
        "enforceWorkflow",
    ),
}


def without_agent_results(root: Path) -> None:
    """Remove the agent-results settings (#52) that ``harness init`` writes. The thesis flows
    document the behaviour before them (for example a broken baseline that blocks); the
    ``agent-results`` flow shows the written defaults."""
    import yaml  # a dependency of the package

    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text())
    for section, keys in AGENT_RESULTS_KEYS.items():
        for key in keys:
            config.get(section, {}).pop(key, None)
    for section in (
        "planning",
        "context",
        "budget",
        "memory",
        "agentRouting",
        "standards",
        "testing",
        "architecture",
        "friction",
    ):
        config.pop(section, None)
    path.write_text(yaml.safe_dump(config, sort_keys=False))


def current_digest(t: Transcript, flow: str, root: Path, run_id: str) -> str:
    status = t.json(flow, root, ["status", "--path", ".", "--run", run_id], 0)
    return str(status["execution"]["changeSetDigest"])


def readme_project(root: Path) -> Path:
    """The project created by the README quickstart, byte for byte."""
    (root / "src" / "pricing").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "pricing" / "__init__.py").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "    return subtotal\n"
    )
    (root / "tests" / "test_pricing.py").write_text(
        "from pricing import apply_discount\n\n\n"
        "def test_below_threshold() -> None:\n"
        "    assert apply_discount(99, 100, 0.1) == 99\n"
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "pricing-demo"\nversion = "0.1.0"\n\n'
        '[tool.pytest.ini_options]\npythonpath = ["src"]\n'
    )
    (root / ".gitignore").write_text(".harness/\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")
    return root


def flow_quickstart(t: Transcript, root: Path) -> None:
    """The README quickstart, command for command, then the other trace formats."""
    flow = "quickstart"
    readme_project(root)
    shutil.copyfile(GUIDE_TASK, root / "task.yaml")
    t.run(flow, root, ["init"], 0)
    t.run(flow, root, ["task", "create", "--file", "task.yaml"], 0)
    run_id = t.json(flow, root, ["run", "start", "--task", "task_discount_rule"], 4)["executionId"]
    status = t.json(flow, root, ["status", "--run", run_id], 0)
    if status["gate"]["status"] != "PASSED":
        t.ok = False
        print(f"[BAD] {flow}: expected a PASSED gate, got {status['gate']['status']}")
    digest = status["execution"]["changeSetDigest"]
    t.run(
        flow,
        root,
        [
            "gate",
            "decide",
            "--run",
            run_id,
            "--decision",
            "APPROVE",
            "--change-set-digest",
            digest,
            "--actor",
            "you",
            "--rationale",
            "Criteria covered by tests",
        ],
        0,
    )
    t.run(flow, root, ["trace", "--run", run_id, "--format", "markdown", "--output", "trace.md"], 0)
    # Beyond the README: the other inspection commands and trace formats.
    t.run(flow, root, ["inspect"], 0)
    t.run(flow, root, ["doctor", "--path", ".", "--json"], 0)
    t.run(flow, root, ["config", "validate"], 0)
    t.run(flow, root, ["findings", "list", "--run", run_id], 0)
    t.run(flow, root, ["evidence", "list", "--run", run_id], 0)
    for fmt in ("json", "jsonl", "sarif"):
        t.run(
            flow, root, ["trace", "--run", run_id, "--format", fmt, "--output", f"trace.{fmt}"], 0
        )
    t.run(flow, root, ["retrospect", "--run", run_id], 0)


def flow_later_change(t: Transcript, root: Path) -> None:
    """Thesis flow 4: a change after a passing gate invalidates the pending approval."""
    flow = "later-change"
    python_project(root)
    (root / "task.yaml").write_text(PY_TASK)
    t.run(flow, root, ["init", "--path", "."], 0)
    without_agent_results(root)
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
    ChangeSet. Verification fails; the baseline is repaired without touching the owned files, so
    the ChangeSet digest does not change; the retry passes, the gate counts the latest attempt of
    each validator and the run closes with an ordinary APPROVE (issue #2, fixed in 0.9.0)."""
    flow = "broken-baseline"
    python_project(root)
    legacy = root / "tests" / "test_legacy.py"
    legacy.write_text("def test_legacy_behaviour() -> None:\n    assert 1 + 1 == 3\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "legacy test (already broken)")
    (root / "task.yaml").write_text(PY_TASK)
    t.run(flow, root, ["init", "--path", "."], 0)
    without_agent_results(root)
    t.run(flow, root, ["task", "create", "--path", ".", "--file", "task.yaml"], 0)
    started = t.json(
        flow, root, ["run", "start", "--path", ".", "--task", "task_python_add_discount"], 6
    )
    run_id = started["executionId"]
    t.run(flow, root, ["findings", "list", "--path", ".", "--run", run_id], 0)
    before = current_digest(t, flow, root, run_id)
    # A different size than the broken version: Python reuses the bytecode cache of a source
    # with the same size rewritten within the same second.
    legacy.write_text("def test_legacy_behaviour() -> None:\n    assert 1 + 1 == 2  # repaired\n")
    t.run(flow, root, ["run", "continue", "--path", ".", "--run", run_id], 4)
    status = t.json(flow, root, ["status", "--path", ".", "--run", run_id], 0)
    digest = status["execution"]["changeSetDigest"]
    if digest != before or status["gate"]["status"] != "PASSED":
        t.ok = False
        print(
            f"[BAD] {flow}: expected unchanged digest and PASSED gate, got {status['gate']['status']}"
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
            "Baseline repaired outside the ChangeSet; the retry passed",
        ],
        0,
    )


def flow_review_exception(t: Transcript, root: Path) -> None:
    """A blocking finding of the independent review (a hard-coded secret, CRITICAL) keeps the gate
    FAILED: APPROVE is refused with exit 5 and only a justified APPROVE_EXCEPTION closes the run."""
    flow = "review-exception"
    python_project(root)
    (root / "task.yaml").write_text(
        PY_TASK.replace("task_python_add_discount", "task_python_secret").replace(
            "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal",
            '            password = "supersecret123"\n'
            "            _ = password\n"
            "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal",
        )
    )
    t.run(flow, root, ["init", "--path", "."], 0)
    without_agent_results(root)
    t.run(flow, root, ["task", "create", "--path", ".", "--file", "task.yaml"], 0)
    run_id = t.json(flow, root, ["run", "start", "--path", ".", "--task", "task_python_secret"], 4)[
        "executionId"
    ]
    status = t.json(flow, root, ["status", "--path", ".", "--run", run_id], 0)
    if status["gate"]["status"] != "FAILED":
        t.ok = False
        print(f"[BAD] {flow}: expected a FAILED gate, got {status['gate']['status']}")
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
            "human.security",
            "--rationale",
            "Demonstration fixture: the value is not a real credential",
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


def approve(t: Transcript, flow: str, root: Path, run_id: str, rationale: str) -> None:
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
            rationale,
        ],
        0,
    )


def flow_memory(t: Transcript, root: Path) -> None:
    """Governed memory, a decided recommendation and reported usage.

    A proposal enters no context until a person approves it; an expired record is listed as
    excluded; an accepted recommendation becomes context of the next run; an invalidated record
    leaves the context; and a command provider that reports its usage fills the token metrics.
    """
    flow = "memory"
    python_project(root)
    (root / "task.yaml").write_text(PY_TASK)
    here = ["--path", "."]
    t.run(flow, root, ["init", *here], 0)
    without_agent_results(root)
    proposal = t.json(
        flow,
        root,
        [
            "memory",
            "add",
            *here,
            "--level",
            "project",
            "--key",
            "money.rounding",
            "--value",
            "Round money half up to two places.",
            "--actor",
            "human.author",
        ],
        0,
    )
    freeze = t.json(
        flow,
        root,
        [
            "memory",
            "add",
            *here,
            "--level",
            "normative",
            "--key",
            "release.freeze",
            "--value",
            "No dependency upgrades during the freeze.",
            "--valid-until",
            "2020-01-01T00:00:00+00:00",
            "--approve",
            "--actor",
            "human.lead",
        ],
        0,
    )
    # A task record without its task is a configuration error.
    t.run(flow, root, ["memory", "add", *here, "--level", "task", "--key", "k", "--value", "v"], 2)
    rule = t.json(
        flow,
        root,
        ["memory", "approve", *here, "--memory", proposal["memoryId"], "--actor", "human.lead"],
        0,
    )
    # Approving an approved record is a policy violation.
    t.run(flow, root, ["memory", "approve", *here, "--memory", rule["memoryId"]], 5)

    t.run(flow, root, ["task", "create", *here, "--file", "task.yaml"], 0)
    first = t.json(flow, root, ["run", "start", *here, "--task", "task_python_add_discount"], 4)[
        "executionId"
    ]
    manifest = t.json(flow, root, ["memory", "manifest", *here, "--run", first], 0)
    t.check(
        flow,
        [item["memoryId"] for item in manifest["records"]] == [rule["memoryId"]],
        "the first run applied the approved rule only",
    )
    t.check(
        flow,
        {item["memoryId"]: item["reason"] for item in manifest["excluded"]}
        == {proposal["memoryId"]: "superseded", freeze["memoryId"]: "expired"},
        "the proposal is recorded as superseded and the old rule as expired",
    )
    approve(t, flow, root, first, "Validators passed")

    recommendation = t.json(flow, root, ["recommendation", "list", *here, "--run", first], 0)[0][
        "recommendation"
    ]["recommendationId"]
    decide = ["recommendation", "decide", *here, "--run", first, "--recommendation", recommendation]
    accepted = t.json(
        flow,
        root,
        [
            *decide,
            "--decision",
            "ACCEPT",
            "--actor",
            "human.lead",
            "--rationale",
            "No findings in this run; keep the controls as they are.",
        ],
        0,
    )
    # A recommendation takes one decision.
    t.run(flow, root, [*decide, "--decision", "REJECT", "--rationale", "Changed my mind"], 5)

    (root / "task-2.yaml").write_text(
        FOLLOW_UP_TASK.format(
            task_id="task_python_above_threshold",
            case="a subtotal above the threshold",
            test="test_above_threshold",
            subtotal=200,
            expected=180,
        )
    )
    t.run(flow, root, ["task", "create", *here, "--file", "task-2.yaml"], 0)
    second = t.json(
        flow, root, ["run", "start", *here, "--task", "task_python_above_threshold"], 4
    )["executionId"]
    manifest = t.json(flow, root, ["memory", "manifest", *here, "--run", second], 0)
    t.check(
        flow,
        {item["memoryId"] for item in manifest["records"]}
        == {rule["memoryId"], accepted["memoryId"]},
        "the second run applied the rule and the accepted recommendation",
    )
    approve(t, flow, root, second, "Validators passed")

    tombstone = t.json(
        flow,
        root,
        [
            "memory",
            "invalidate",
            *here,
            "--memory",
            rule["memoryId"],
            "--reason",
            "Rounding moved to the billing service.",
            "--actor",
            "human.lead",
        ],
        0,
    )
    listing = t.json(flow, root, ["memory", "list", *here], 0)
    t.check(
        flow,
        {item["record"]["memoryId"]: item["status"] for item in listing}
        == {
            proposal["memoryId"]: "superseded",
            freeze["memoryId"]: "expired",
            rule["memoryId"]: "superseded",
            accepted["memoryId"]: "active",
            tombstone["memoryId"]: "expired",
        },
        "after the invalidation only the accepted recommendation stays active",
    )

    # A command provider that reports its usage fills the token and cost metrics. The flow is
    # about usage and runs on Linux and Windows hosts without a sandbox mechanism, so it turns the
    # agent sandbox that init enforces off (with enforce such a host blocks IMPLEMENTATION).
    (root / "usage_adapter.py").write_text(USAGE_ADAPTER)
    config = root / ".harness" / "project.yaml"
    config.write_text(
        config.read_text().replace("agentSandbox: enforce", "agentSandbox: 'off'")
        + "agentProviders:\n  usage_fixture:\n    kind: command\n"
        + "    command: [python, usage_adapter.py]\n    model: usage-fixture\n"
    )
    (root / "task-3.yaml").write_text(
        FOLLOW_UP_TASK.format(
            task_id="task_python_zero",
            case="a subtotal of zero",
            test="test_zero_subtotal",
            subtotal=0,
            expected=0,
        )
    )
    t.run(flow, root, ["task", "create", *here, "--file", "task-3.yaml"], 0)
    third = t.json(
        flow,
        root,
        ["run", "start", *here, "--task", "task_python_zero", "--provider", "usage_fixture"],
        4,
    )["executionId"]
    metrics = t.json(flow, root, ["status", *here, "--run", third], 0)["metrics"]
    t.check(
        flow,
        metrics["tokens.input"]["value"] == 1200
        and metrics["tokens.input"]["quality"] == "REPORTED"
        and metrics["cost.usd"]["quality"] == "REPORTED",
        "token and cost metrics carry the usage the provider reported",
    )
    approve(t, flow, root, third, "Validators passed")


VAGUE_TASK = """\
taskId: task_python_vague
title: Discount
intent: Add a discount.
acceptanceCriteria:
  - criterionId: ac_works
    text: It works.
implementation:
  mode: patch
  patches:
    - path: src/sample/pricing.py
      operation: replace
      content: |
        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:
            return subtotal * (1 - rate) if subtotal >= threshold else subtotal
"""

CLARIFICATION = """\
answers:
  Q-1: apply_discount(100, 100, 0.1) returns 90 and apply_discount(99, 100, 0.1) returns 99.
  Q-2: Only the threshold rule; rounding and currencies are out of scope.
"""


ONE_SENTENCE_TASK = """\
taskId: task_python_one_sentence
title: Discount rules
intent: Describe the discount rule in code.
implementation:
  mode: patch
  patches:
    - path: src/sample/rules.py
      operation: create
      content: |
        THRESHOLD_IS_INCLUSIVE = True
"""

ELICITATION = """\
answers:
  Q-1: |
    - sample.rules.THRESHOLD_IS_INCLUSIVE equals True.
    - The existing tests in tests/test_pricing.py pass.
  Q-6: Changing apply_discount.
"""


def flow_clarification(t: Transcript, root: Path) -> None:
    """A task whose only criterion is "It works." blocks INTENT under the enforce policy written
    by init (exit 6). A person answers the questions; the revised task passes INTENT on
    run continue and the run reaches DECISION. Unknown question ids exit with 2 and clarifying a
    task whose run is past INTENT exits with 5. A one-sentence task without criteria is accepted
    under enforce; INTENT asks the seven C0 questions (exit 6), and the answer about results
    becomes its criteria, so run continue reaches DECISION."""
    flow = "clarification"
    python_project(root)
    (root / "task.yaml").write_text(VAGUE_TASK)
    (root / "answers.yaml").write_text(CLARIFICATION)
    (root / "unknown.yaml").write_text("answers:\n  Q-9: Not a question of this task.\n")
    here = ["--path", "."]
    t.run(flow, root, ["init", *here], 0)
    t.run(flow, root, ["task", "create", *here, "--file", "task.yaml"], 0)
    run_id = t.json(flow, root, ["run", "start", *here, "--task", "task_python_vague"], 6)[
        "executionId"
    ]
    questions = t.json(flow, root, ["task", "questions", *here, "--task", "task_python_vague"], 0)
    t.check(
        flow,
        [item["ruleId"] for item in (questions["openRequest"] or {}).get("questions", [])]
        == ["C1", "T1"],
        "INTENT asked about the unobservable criterion and the unbroken scope",
    )
    clarify = ["task", "clarify", *here, "--task", "task_python_vague", "--actor", "human.author"]
    t.run(flow, root, [*clarify, "--file", "unknown.yaml"], 2)
    t.run(flow, root, [*clarify, "--file", "answers.yaml"], 0)
    t.run(flow, root, ["run", "continue", *here, "--run", run_id], 4)
    approve(t, flow, root, run_id, "Clarified criteria covered by the validators")
    t.run(flow, root, [*clarify, "--file", "answers.yaml"], 5)

    (root / "one-sentence.yaml").write_text(ONE_SENTENCE_TASK)
    (root / "elicitation.yaml").write_text(ELICITATION)
    task_id = "task_python_one_sentence"
    created = t.json(flow, root, ["task", "create", *here, "--file", "one-sentence.yaml"], 0)
    t.check(
        flow,
        created["acceptanceCriteria"] == [] and created["criteriaPending"] is True,
        "a task without acceptance criteria is accepted under enforce, marked criteriaPending",
    )
    run_id = t.json(flow, root, ["run", "start", *here, "--task", task_id], 6)["executionId"]
    questions = t.json(flow, root, ["task", "questions", *here, "--task", task_id], 0)
    t.check(
        flow,
        [item["ruleId"] for item in (questions["openRequest"] or {}).get("questions", [])]
        == ["C0"] * 7,
        "INTENT asked the seven C0 questions that elicit the acceptance criteria",
    )
    elicit = ["task", "clarify", *here, "--task", task_id, "--actor", "human.author"]
    revised = t.json(flow, root, [*elicit, "--file", "elicitation.yaml"], 0)["task"]
    t.check(
        flow,
        len(revised["acceptanceCriteria"]) == 2 and "criteriaPending" not in revised,
        "the answer about results became two acceptance criteria",
    )
    t.run(flow, root, ["run", "continue", *here, "--run", run_id], 4)
    approve(t, flow, root, run_id, "Elicited criteria covered by the validators")


UNTRACED_TASK = """\
taskId: task_python_untraced
title: Apply a percentage discount above a threshold
intent: Apply a percentage discount only when the subtotal reaches the threshold.
requirements:
  - "A1. A subtotal at or above the threshold is reduced by the rate."
  - "A2. A subtotal below the threshold is unchanged."
acceptanceCriteria:
  - criterionId: ac_at_threshold
    text: apply_discount(100, 100, 0.1) returns 90.
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

        def test_a1_at_threshold() -> None:
            assert apply_discount(100, 100, 0.1) == 90
"""


def flow_traceability(t: Transcript, root: Path) -> None:
    """Requirement A2 is named by no test: under the enforce policy written by init it becomes a
    HIGH finding of traceability.requirements, the gate is FAILED and APPROVE exits with 5. The
    mapping of A1 to its test is recorded as VERIFICATION evidence."""
    flow = "traceability"
    python_project(root)
    (root / "task.yaml").write_text(UNTRACED_TASK)
    here = ["--path", "."]
    t.run(flow, root, ["init", *here], 0)
    t.run(flow, root, ["task", "create", *here, "--file", "task.yaml"], 0)
    run_id = t.json(flow, root, ["run", "start", *here, "--task", "task_python_untraced"], 4)[
        "executionId"
    ]
    status = t.json(flow, root, ["status", *here, "--run", run_id], 0)
    t.check(flow, status["gate"]["status"] == "FAILED", "the gate is FAILED")
    findings = [
        item
        for item in t.json(flow, root, ["findings", "list", *here, "--run", run_id], 0)
        if item["validatorId"] == "traceability.requirements"
    ]
    t.check(
        flow,
        [(item["ruleId"], item["severity"]) for item in findings]
        == [("traceability.requirement-untested", "HIGH")]
        and "A2" in findings[0]["message"],
        "one HIGH finding names the untraced requirement A2",
    )
    digest = current_digest(t, flow, root, run_id)
    decide = ["gate", "decide", *here, "--run", run_id, "--change-set-digest", digest]
    decide += ["--actor", "human.reviewer"]
    t.run(flow, root, [*decide, "--decision", "APPROVE", "--rationale", "Tests pass"], 5)
    # The rejected run ends FAILED, which gate decide reports with exit 6.
    t.run(flow, root, [*decide, "--decision", "REJECT", "--rationale", "A2 has no test"], 6)


CORRECTING_AGENT = """\
import json
import sys
from pathlib import Path

# A command provider for the corrections flow. "fix" leaves apply_discount unchanged until the
# request carries feedback, "stubborn" never changes it, "flaky" fails once as an overloaded
# service would and then implements the rule. None of them calls a model.
mode = sys.argv[1]
request = json.load(sys.stdin)
state = Path(".agent-calls")
calls = int(state.read_text()) + 1 if state.exists() else 1
state.write_text(str(calls))
if mode == "flaky" and calls == 1:
    sys.stderr.write("upstream error: the model is overloaded, try again later\\n")
    sys.exit(1)
fixed = mode == "flaky" or (mode == "fix" and "feedback" in request)
body = "subtotal * (1 - rate) if subtotal >= threshold else subtotal" if fixed else "subtotal"
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    f"    return {body}\\n"
)
Path("tests/test_threshold.py").write_text(
    "from sample import apply_discount\\n\\n\\n"
    "def test_at_threshold() -> None:\\n"
    "    assert apply_discount(100, 100, 0.1) == 90\\n"
)
print(json.dumps({"status": "PASSED", "summary": "Implemented the threshold discount"}))
"""

CORRECTION_TASK = """\
taskId: {task_id}
title: Threshold discount
intent: Apply the configured discount at or above the threshold.
acceptanceCriteria:
  - criterionId: ac_threshold
    text: apply_discount(100, 100, 0.1) returns 90.
metadata:
  ownedPaths: [src/sample/pricing.py, tests/test_threshold.py]
"""


def flow_corrections(t: Transcript, root: Path) -> None:
    """The feedback loop that init enables for command providers. An agent whose first change
    fails VERIFICATION gets the validator output as feedback, corrects it and the run reaches
    DECISION (exit 4) with one automatic correction and one unsupported-claim finding. An agent
    that never corrects stops in VERIFICATION after the two corrections init allows (exit 6). A
    call that fails because the service is overloaded is repeated and the run reaches DECISION."""
    flow = "corrections"
    python_project(root)
    here = ["--path", "."]
    t.run(flow, root, ["init", *here], 0)
    without_agent_results(root)
    (root / "agent.py").write_text(CORRECTING_AGENT)
    config = root / ".harness" / "project.yaml"
    # The flow runs on hosts without a sandbox mechanism (CI Linux without bwrap, Windows).
    config.write_text(
        config.read_text()
        .replace("providerRetryDelaySeconds: 60", "providerRetryDelaySeconds: 0")
        .replace("agentSandbox: enforce", "agentSandbox: 'off'")
        + "agentProviders:\n"
        + "".join(
            f"  {mode}:\n    kind: command\n    command: [python, agent.py, {mode}]\n"
            for mode in ("fix", "stubborn", "flaky")
        )
    )

    def start(task_id: str, provider: str, expect: int) -> str:
        (root / f"{task_id}.yaml").write_text(CORRECTION_TASK.format(task_id=task_id))
        t.run(flow, root, ["task", "create", *here, "--file", f"{task_id}.yaml"], 0)
        (root / ".agent-calls").unlink(missing_ok=True)
        started = ["run", "start", *here, "--task", task_id, "--provider", provider]
        return str(t.json(flow, root, started, expect)["executionId"])

    def metrics(run_id: str) -> dict[str, Any]:
        status = t.json(flow, root, ["status", *here, "--run", run_id], 0)
        return {key: value["value"] for key, value in status["metrics"].items()}

    fixed = start("task_corrected", "fix", 4)
    values = metrics(fixed)
    t.check(
        flow,
        values["correction.verification_cycles"] == 1
        and values["implementation.attempts"] == 2
        and values["agent.unsupported_claims"] == 1,
        "a failed verification returned to IMPLEMENTATION once and the agent corrected it",
    )
    approve(t, flow, root, fixed, "Corrected change passed verification")

    stubborn = start("task_stubborn", "stubborn", 6)
    values = metrics(stubborn)
    t.check(
        flow,
        values["correction.cycles"] == 2 and values["implementation.attempts"] == 3,
        "after two corrections the run stopped in VERIFICATION",
    )

    flaky = start("task_flaky", "flaky", 4)
    values = metrics(flaky)
    t.check(
        flow,
        values["agent.transient_retries"] == 1 and values["agent.invocations"] == 2,
        "the overloaded call was repeated once",
    )


def flow_integrity(t: Transcript, root: Path) -> None:
    """The integrity settings init writes (wave 1): an agent identity cannot decide, the task of
    an open run cannot be replaced, the record verifies, and an edited event is reported by
    status and verify and refused by trace."""
    flow = "integrity"
    python_project(root)
    (root / "task.yaml").write_text(PY_TASK)
    here = ["--path", "."]
    t.run(flow, root, ["init", *here], 0)
    without_agent_results(root)
    validated = t.json(flow, root, ["config", "validate", *here], 0)
    t.check(
        flow,
        any(item.startswith("runtime.maxParallel") for item in validated["warnings"]),
        "config validate reports the declarative runtime.maxParallel",
    )
    t.run(flow, root, ["task", "create", *here, "--file", "task.yaml"], 0)
    run_id = t.json(flow, root, ["run", "start", *here, "--task", "task_python_add_discount"], 4)[
        "executionId"
    ]
    digest = current_digest(t, flow, root, run_id)
    decide = ["gate", "decide", *here, "--run", run_id, "--change-set-digest", digest]
    t.run(
        flow,
        root,
        [
            *decide,
            "--decision",
            "APPROVE_EXCEPTION",
            "--actor",
            "agent.claude-code",
            "--rationale",
            "self-approval",
        ],
        5,
    )
    status = t.json(flow, root, ["status", *here, "--run", run_id], 0)
    t.check(flow, status["humanDecision"] is None, "the agent's decision was not recorded")
    t.run(flow, root, ["task", "create", *here, "--file", "task.yaml"], 5)
    t.run(flow, root, ["verify", *here, "--run", run_id], 0)
    approve(t, flow, root, run_id, "Discount rule reviewed")
    t.run(flow, root, ["verify", *here], 0)
    # runtime.stateDir (written by init) keeps the registry outside the workspace.
    database = Path(validated["ladder"]["state"]["database"])
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE events SET payload_json=? WHERE execution_id=? AND event_type=?",
            (json.dumps({"forged": True}), run_id, "human.decision.recorded"),
        )
    connection.close()
    status = t.json(flow, root, ["status", *here, "--run", run_id], 0)
    t.check(flow, status["eventChainValid"] is False, "status reports the edited event")
    t.run(flow, root, ["verify", *here, "--run", run_id], 6)
    t.run(flow, root, ["trace", *here, "--run", run_id, "--format", "json"], 6)


def flow_delivery(t: Transcript, root: Path) -> None:
    """Wave 4: the approved ChangeSet becomes one commit with trailers on harness/<run> (the
    closure commit init enables), the evidence bundle verifies without the workspace, and
    verify-approval passes for the closure commit with the bundle, fails without an approval
    (exit 5) and fails once a later commit changes the range (exit 5)."""
    flow = "delivery"
    python_project(root)
    (root / "task.yaml").write_text(PY_TASK)
    t.run(flow, root, ["init", "--path", "."], 0)
    t.run(flow, root, ["task", "create", "--path", ".", "--file", "task.yaml"], 0)
    run_id = t.json(
        flow, root, ["run", "start", "--path", ".", "--task", "task_python_add_discount"], 4
    )["executionId"]
    base = git_output(root, "rev-parse", "HEAD")
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
            "you",
            "--rationale",
            "Criteria covered by tests",
        ],
        0,
    )
    branch = f"harness/{run_id}"
    message = git_output(root, "log", "-1", "--format=%B", branch)
    t.check(
        flow,
        f"Harness-Run: {run_id}" in message and f"Harness-ChangeSet: {digest}" in message,
        "the closure commit carries the run and the approved digest as trailers",
    )
    t.check(flow, git_output(root, "rev-parse", "HEAD") == base, "the current branch did not move")
    t.run(flow, root, ["export", "--path", ".", "--run", run_id, "--bundle", "evidence.tar.gz"], 0)
    t.run(flow, root, ["verify", "--bundle", "evidence.tar.gz"], 0)
    approval = ["verify-approval", "--path", ".", "--base", base, "--no-workspace"]
    t.run(flow, root, [*approval, "--head", branch, "--bundle", "evidence.tar.gz"], 0)
    t.run(flow, root, [*approval, "--head", branch], 5)
    git(root, "checkout", "-q", "--force", branch)  # the working tree holds the same change
    with (root / "src" / "sample" / "pricing.py").open("a") as handle:
        handle.write("# a later edit nobody approved\n")
    git(root, "commit", "-qam", "later edit")
    t.run(flow, root, [*approval, "--head", "HEAD", "--bundle", "evidence.tar.gz"], 5)


def flow_agent_results(t: Transcript, root: Path) -> None:
    """The agent-results settings init writes (wave 2, #52), with the simulated provider: a
    failure the baseline already has does not block, harness check runs the gate without
    recording anything, and a rejected run's changes are quarantined (stop the line)."""
    flow = "agent-results"
    python_project(root)
    legacy = root / "tests" / "test_legacy.py"
    legacy.write_text("def test_legacy_behaviour() -> None:\n    assert 1 + 1 == 3\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "legacy test (already broken)")
    (root / "task.yaml").write_text(PY_TASK)
    here = ["--path", "."]
    t.run(flow, root, ["init", *here], 0)
    validated = t.json(flow, root, ["config", "validate", *here], 0)
    t.check(
        flow,
        validated["agentResults"]["checks"].get("differential") is True,
        "init enables the comparison with the baseline",
    )
    t.run(flow, root, ["task", "create", *here, "--file", "task.yaml"], 0)
    run_id = t.json(flow, root, ["run", "start", *here, "--task", "task_python_add_discount"], 4)[
        "executionId"
    ]
    status = t.json(flow, root, ["status", *here, "--run", run_id], 0)
    t.check(
        flow,
        status["gate"]["status"] == "PASSED",
        "the failure the baseline already had does not block the gate",
    )
    # harness check runs the gate's validators: the legacy failure is still a failure there.
    t.run(flow, root, ["check", *here, "--run", run_id], 6)
    digest = current_digest(t, flow, root, run_id)
    t.run(
        flow,
        root,
        [
            "gate",
            "decide",
            *here,
            "--run",
            run_id,
            "--decision",
            "REJECT",
            "--change-set-digest",
            digest,
            "--actor",
            "human.reviewer",
            "--rationale",
            "Not this change",
        ],
        6,
    )
    pricing = (root / "src" / "sample" / "pricing.py").read_text()
    t.check(flow, "1 - rate" not in pricing, "the rejected change was quarantined")


LADDER_CLI = """\
import json
import sys

from sample import apply_discount

print(json.dumps({"total": apply_discount(float(sys.argv[1]), 100, 0.1)}))
"""

LADDER_PROBE = """\
  - id: cli
    command: [python, -m, sample.cli, "{amount}"]
    output: json
    variants:
      - {name: below, values: {amount: "50"}, env: {PYTHONPATH: src}}
      - {name: above, values: {amount: "200"}, env: {PYTHONPATH: src}}
    assertions:
      - {kind: exitCode, equals: 0}
      - {kind: jsonPath, path: "$.total", present: true}
      - {kind: differs, path: "$.total"}
"""

LADDER_TASK = (
    """\
taskId: task_ladder
title: Discount at the threshold, certified
intent: Apply a percentage discount only when the subtotal reaches the threshold.
acceptanceCriteria:
  - criterionId: ac_unit
    text: apply_discount(100, 100, 0.1) returns 90.
    verification: {level: L1}
  - criterionId: ac_cli
    text: The command line prints the discounted total as JSON.
    verification: {level: L3, probe: cli}
  - criterionId: ac_e2e
    text: The checkout flow shows the discount end to end.
    verification: {level: L4, deferred: CI job e2e}
  - criterionId: ac_look
    text: The receipt shows the discount line.
    verification: {level: L5, manual: The receipt layout shows the discount line}
probes:
"""
    + LADDER_PROBE
    + """\
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

        def test_ac_unit_at_threshold() -> None:
            assert apply_discount(100, 100, 0.1) == 90
"""
)

UNREACHED_TASK = """\
taskId: task_unreached
title: Rounding to cents
intent: Round the discounted total to cents.
acceptanceCriteria:
  - criterionId: ac_rounding
    text: apply_discount(10.005, 1, 0.1) returns 9.0.
    verification: {level: L1}
implementation:
  mode: patch
  patches:
    - path: src/sample/pricing.py
      operation: replace
      content: |
        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:
            value = subtotal * (1 - rate) if subtotal >= threshold else subtotal
            return round(value, 2)
"""

DEVICE_TASK = """\
taskId: task_device
title: The total on a device
intent: Show the discounted total on the device screen.
acceptanceCriteria:
  - criterionId: ac_screen_total
    text: apply_discount(200, 100, 0.1) returns 180.
  - criterionId: ac_device
    text: The device screen shows the total.
    verification: {level: L3, probe: device}
probes:
  - id: device
    command: [device-lab, run, total-screen]
    assertions:
      - {kind: exitCode, equals: 0}
implementation:
  mode: patch
  patches:
    - path: tests/test_screen.py
      operation: create
      content: |
        from sample import apply_discount


        def test_ac_screen_total() -> None:
            assert apply_discount(200, 100, 0.1) == 180
"""

LADDER_JUNIT = """\
<?xml version="1.0"?>
<testsuites><testsuite name="e2e" tests="1">
<testcase classname="checkout" name="test_ac_e2e_discount"/>
</testsuite></testsuites>
"""


def flow_ladder(t: Transcript, root: Path) -> None:
    """Wave 5, with the defaults init writes: a task declares the rung of each criterion (a unit
    test, a probe of the command line with two variants, an end-to-end check only CI runs, a
    check only a person makes). The run reaches DECISION certified PARTIAL; APPROVE without the
    checklist exits 5 and with it the run closes; a JUnit report closes the deferred item and
    the run is CERTIFIED. A task whose declared L1 rung no test reaches (no test names the
    criterion) stops in VERIFICATION, not certified (exit 6); a task whose probe cannot run waits
    for a person in
    PLANNING (exit 6) and continues uncertified (exit 4). config lint reports an instruction
    file that tells the agent to skip the hooks (exit 6)."""
    flow = "ladder"
    python_project(root)
    (root / "src" / "sample" / "cli.py").write_text(LADDER_CLI)
    git(root, "add", ".")
    git(root, "commit", "-qm", "command line")
    here = ["--path", "."]
    t.run(flow, root, ["init", *here], 0)
    (root / "task.yaml").write_text(LADDER_TASK)
    t.run(flow, root, ["task", "create", *here, "--file", "task.yaml"], 0)
    run_id = t.json(flow, root, ["run", "start", *here, "--task", "task_ladder"], 4)["executionId"]
    shown = t.json(flow, root, ["verification", "show", *here, "--run", run_id], 0)
    t.check(
        flow,
        shown["preflight"]["status"] == "PARTIAL" and shown["certification"]["status"] == "PARTIAL",
        "the preflight and the certification are PARTIAL: CI and a person are still to come",
    )
    digest = current_digest(t, flow, root, run_id)
    decide = ["gate", "decide", *here, "--run", run_id, "--change-set-digest", digest]
    decide += ["--actor", "human.reviewer", "--decision", "APPROVE", "--rationale", "Checked"]
    t.run(flow, root, decide, 5)
    t.run(flow, root, [*decide, "--check", "ac_look"], 0)
    (root / "e2e.xml").write_text(LADDER_JUNIT)
    attach = ["evidence", "attach", *here, "--run", run_id, "--item", "D-ac_e2e"]
    t.run(flow, root, [*attach, "--file", "e2e.xml", "--actor", "human.ci"], 0)
    shown = t.json(flow, root, ["verification", "show", *here, "--run", run_id], 0)
    t.check(
        flow,
        shown["certification"]["status"] == "CERTIFIED",
        "the attached JUnit report closed the deferred item: CERTIFIED",
    )

    (root / "unreached.yaml").write_text(UNREACHED_TASK)
    t.run(flow, root, ["task", "create", *here, "--file", "unreached.yaml"], 0)
    unreached = t.json(flow, root, ["run", "start", *here, "--task", "task_unreached"], 6)
    shown = t.json(
        flow, root, ["verification", "show", *here, "--run", unreached["executionId"]], 0
    )
    t.check(
        flow,
        shown["certification"]["status"] == "NOT_CERTIFIED"
        and unreached["currentPhase"] == "VERIFICATION",
        "a declared L1 rung no test reaches stops VERIFICATION, not certified",
    )

    # The device lab's command is granted to the probes, but this machine does not have it.
    # Under governance.phaseCapabilities (#4) project grants narrow the profiles; a scope no
    # profile grants is added with capabilities.extend.
    config = root / ".harness" / "project.yaml"
    config.write_text(
        config.read_text().replace(
            "  grants: []",
            "  grants: []\n  extend:\n  - capability: process.execute\n    scope: [device-lab]",
        )
    )
    (root / "device.yaml").write_text(DEVICE_TASK)
    t.run(flow, root, ["task", "create", *here, "--file", "device.yaml"], 0)
    device = t.json(flow, root, ["run", "start", *here, "--task", "task_device"], 6)
    t.check(
        flow,
        device["currentPhase"] == "PLANNING" and device["status"] == "BLOCKED",
        "a probe that cannot run makes the preflight UNAVAILABLE before any change",
    )
    t.run(
        flow,
        root,
        [
            "verification",
            "decide",
            *here,
            "--run",
            device["executionId"],
            "--continue-uncertified",
            "--actor",
            "human.lead",
            "--rationale",
            "No device here; the device lab verifies it",
        ],
        4,
    )
    (root / "AGENTS.md").write_text("Commit with git commit --no-verify when the hooks are slow.\n")
    t.run(flow, root, ["config", "lint", *here], 6)


class _FakeForge(BaseHTTPRequestHandler):
    """A GitLab REST v4 stand-in on 127.0.0.1: it records every request and answers like
    GitLab would, so the forge flow reaches no network."""

    calls: list[tuple[str, str, Any]] = []

    def _answer(self, status: int, value: Any) -> None:
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        self.calls.append((self.command, self.path, body))
        return body

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self._record()
        self._answer(200, [])

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        self._record()
        if self.path.endswith("/merge_requests"):
            self._answer(201, {"iid": 42, "web_url": "https://gitlab.example.invalid/mr/42"})
        else:
            self._answer(201, {"id": 1})

    def do_PUT(self) -> None:  # noqa: N802 - http.server API
        self._record()
        self._answer(200, {"id": 1})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server API
        return


def flow_gitlab(t: Transcript, root: Path) -> None:
    """A project whose origin is on GitLab (#56): the forge is detected from the remote, the
    decision brief is published as a merge request note, a merge request is opened from the
    closure branch with the labels, the commit status follows the run and the findings are
    exported as a GitLab Code Quality report. The REST API is a local fake server: no
    network."""
    flow = "gitlab"
    python_project(root)
    git(root, "remote", "add", "origin", "https://gitlab.example.com/team/shop.git")
    here = ["--path", "."]
    t.run(flow, root, ["init", *here], 0)
    without_agent_results(root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeForge)
    _FakeForge.calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        config = root / ".harness" / "project.yaml"
        config.write_text(
            config.read_text().replace(
                "delivery:\n",
                "delivery:\n  forge:\n"
                f"    apiUrl: http://127.0.0.1:{port}/api/v4\n"
                "    baseBranch: main\n    labels: [governed]\n",
            )
        )
        env = {"GITLAB_TOKEN": "demo-placeholder"}
        (root / "task.yaml").write_text(PY_TASK)
        t.run(flow, root, ["task", "create", *here, "--file", "task.yaml"], 0)
        run_id = t.json(
            flow, root, ["run", "start", *here, "--task", "task_python_add_discount"], 4
        )["executionId"]
        shown = t.json(flow, root, ["pr", "forge", *here], 0)
        t.check(flow, shown["kind"] == "gitlab", "the forge is detected from the origin remote")
        published = t.json(flow, root, ["pr", "publish", *here, "--pr", "7"], 0, env)
        t.check(
            flow,
            published["comment"]["action"] == "created"
            and any(
                call[0] == "POST" and call[1].endswith("/merge_requests/7/notes")
                for call in _FakeForge.calls
            ),
            "the brief is a note on merge request 7",
        )
        created = t.json(flow, root, ["pr", "create", *here, "--run", run_id], 0, env)
        t.check(
            flow,
            created["pullRequest"]["number"] == 42 and created["head"] == f"harness/{run_id}",
            "a merge request is opened from the closure branch",
        )
        head = git_output(root, "rev-parse", "HEAD")
        status = t.json(flow, root, ["pr", "status", *here, "--commit", head], 0, env)
        t.check(flow, status["state"] == "pending", "the commit status waits for the decision")
        t.run(
            flow,
            root,
            ["trace", *here, "--format", "codequality", "--output", "gl-code-quality-report.json"],
            0,
        )
        issues = json.loads((root / "gl-code-quality-report.json").read_text())
        t.check(flow, isinstance(issues, list), "the Code Quality report is a JSON array")
        t.run(flow, root, ["pr", "publish", *here, "--pr", "7"], 2, {"GITLAB_TOKEN": ""})
    finally:
        server.shutdown()
        server.server_close()


TDD_TASK = """\
taskId: {task_id}
title: Threshold discount, test first
intent: Apply the configured discount at or above the threshold.
acceptanceCriteria:
  - criterionId: AC-1
    text: apply_discount(100, 100, 0.1) returns 90.
implementation:
  mode: patch
  patches:
    - path: src/sample/pricing.py
      operation: replace
      content: |
        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:
            return {body}
    - path: tests/test_pricing.py
      operation: append
      content: |

        def test_{test}() -> None:
            assert apply_discount({subtotal}, 100, 0.1) == {expected}
"""


TDD_BODY = "subtotal * (1 - rate) if subtotal >= threshold else subtotal"
TDD_LATE_BODY = "subtotal - subtotal * rate if subtotal >= threshold else subtotal"


def flow_tdd(t: Transcript, root: Path) -> None:
    """testing.strategy: tdd (#56) with the simulated provider. A change whose new test fails
    on the code before it (red), passes after it (green) and leaves the principles checks clean
    (refactor) reaches DECISION; a change whose test already passes before it stops in
    VERIFICATION with tdd.not-red. The standards cards for the touched file are shown."""
    flow = "tdd"
    python_project(root)
    here = ["--path", "."]
    t.run(flow, root, ["init", *here], 0)
    config = root / ".harness" / "project.yaml"
    config.write_text(config.read_text().replace("strategy: auto", "strategy: tdd"))
    shown = t.json(flow, root, ["standards", "show", *here, "--file", "src/sample/pricing.py"], 0)
    t.check(
        flow,
        any(item["id"].startswith("python.") for item in shown["selection"]["implement"]),
        "the python cards apply to the touched file",
    )
    (root / "red.yaml").write_text(
        TDD_TASK.format(
            task_id="task_red", test="at_threshold", subtotal=100, expected=90, body=TDD_BODY
        )
    )
    t.run(flow, root, ["task", "create", *here, "--file", "red.yaml"], 0)
    run_id = t.json(flow, root, ["run", "start", *here, "--task", "task_red"], 4)["executionId"]
    trace = t.json(flow, root, ["trace", *here, "--run", run_id, "--format", "json"], 0)
    tdd = [item for item in trace["validations"] if item["validatorId"] == "harness.tdd"]
    t.check(flow, bool(tdd) and tdd[-1]["status"] == "PASSED", "red, green and refactor recorded")
    approve(t, flow, root, run_id, "Test written first")
    (root / "green.yaml").write_text(
        TDD_TASK.format(
            task_id="task_after", test="far_below", subtotal=10, expected=10, body=TDD_LATE_BODY
        )
    )
    t.run(flow, root, ["task", "create", *here, "--file", "green.yaml"], 0)
    late = t.json(flow, root, ["run", "start", *here, "--task", "task_after"], 6)["executionId"]
    rules = {
        item["ruleId"] for item in t.json(flow, root, ["findings", "list", *here, "--run", late], 0)
    }
    t.check(flow, "tdd.not-red" in rules, "a test that passes before the change is not TDD")


BDD_AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
kind = request.get("kind", "implement")
feature = (
    "Feature: Threshold discount\\n"
    "  Scenario: AC-1 a subtotal at the threshold is discounted\\n"
    "    Given a subtotal of 100 and a threshold of 100\\n"
    "    When the discount of 10 percent applies\\n"
    "    Then the total is 90\\n"
)
results = {
    "clarify": {"questions": []},
    "review": {"findings": []},
    "acceptance": {"tests": [{"path": "features/discount.feature", "content": feature}]},
    "architecture": {"style": "custom", "summary": "No layers.", "layers": [], "allow": {}},
}
if kind == "review" and "outputContract" in request:
    # A reviewer of the review panel (review.panel, #57) answers with its output contract.
    results["review"] = {"verdict": "PASS", "findings": [], "summary": "No finding."}
if kind in results:
    print(json.dumps({"status": "PASSED", "summary": kind, "result": results[kind]}))
    sys.exit(0)
Path("features/steps").mkdir(parents=True, exist_ok=True)
Path("features/steps/discount_steps.py").write_text(
    "STEPS = ['a subtotal of 100 and a threshold of 100', "
    "'the discount of 10 percent applies', 'the total is 90']\\n"
)
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n"
)
print(json.dumps({"status": "PASSED", "summary": "step definitions and code"}))
"""

FEATURE_RUNNER = """\
import re, sys
from pathlib import Path

steps_dir = Path("features/steps")
steps = "\\n".join(p.read_text() for p in steps_dir.glob("*.py")) if steps_dir.is_dir() else ""
missing = [
    match.group(2)
    for feature in Path("features").glob("*.feature")
    for line in feature.read_text().splitlines()
    if (match := re.match(r"\\s*(Given|When|Then|And) (.+)", line)) and match.group(2) not in steps
]
print("undefined steps:", missing)
sys.exit(1 if missing else 0)
"""

BDD_TASK = """\
taskId: task_bdd
title: Threshold discount, behaviour first
intent: Apply the configured discount at or above the threshold.
acceptanceCriteria:
  - criterionId: AC-1
    text: apply_discount(100, 100, 0.1) returns 90.
metadata:
  ownedPaths: [src/sample/pricing.py, features/steps/discount_steps.py]
"""


def flow_bdd(t: Transcript, root: Path) -> None:
    """testing.strategy: bdd (#56) with a fixture command provider. The acceptance call writes
    the criterion as a Gherkin scenario; SPECIFICATION waits for a person (exit 6); the
    approval freezes the feature file and runs the BDD runner before the change (it fails: no
    step is defined); the agent then writes the step definitions and the code, and the run
    reaches DECISION (exit 4). The runner here is a small fixture script; a project uses
    behave, pytest-bdd, Cucumber, SpecFlow or its pack's runner."""
    flow = "bdd"
    python_project(root)
    (root / "agent.py").write_text(BDD_AGENT)
    (root / "run_features.py").write_text(FEATURE_RUNNER)
    git(root, "add", ".")
    git(root, "commit", "-qm", "agent and runner fixtures")
    here = ["--path", "."]
    t.run(flow, root, ["init", *here], 0)
    config = root / ".harness" / "project.yaml"
    config.write_text(
        config.read_text()
        .replace("strategy: auto", "strategy: bdd\n  bddCommand: [python, run_features.py]")
        .replace("agentSandbox: enforce", "agentSandbox: 'off'")
        .replace("agentProvider: simulated", "agentProvider: bdd_agent")
        .replace("providerRetryDelaySeconds: 60", "providerRetryDelaySeconds: 0")
        + "agentProviders:\n  bdd_agent:\n    kind: command\n    command: [python, agent.py]\n"
    )
    (root / "task.yaml").write_text(BDD_TASK)
    t.run(flow, root, ["task", "create", *here, "--file", "task.yaml"], 0)
    run_id = t.json(flow, root, ["run", "start", *here, "--task", "task_bdd"], 6)["executionId"]
    proposal = t.json(flow, root, ["acceptance", "show", *here, "--run", run_id], 0)
    t.check(
        flow,
        proposal["format"] == "gherkin"
        and proposal["tests"][0]["path"] == "features/discount.feature",
        "the acceptance call proposed a Gherkin scenario",
    )
    decided = t.json(
        flow,
        root,
        [
            "acceptance",
            "decide",
            *here,
            "--run",
            run_id,
            "--decision",
            "APPROVE",
            "--digest",
            proposal["digest"],
            "--actor",
            "human.reviewer",
            "--rationale",
            "The scenario states the criterion",
        ],
        4,
    )
    t.check(
        flow,
        decided["acceptanceTests"]["failBefore"]["status"] == "FAILED",
        "the frozen scenario fails before the step definitions exist",
    )


REVIEWER = """\
import json, sys

request = json.load(sys.stdin)
reviewer = (request.get("reviewer") or {}).get("id")
if MODE == "unknown":
    print(json.dumps({"status": "PASSED", "summary": "no contract", "result": {"findings": []}}))
    sys.exit(0)
findings = []
diff = (request.get("slice") or {}).get("diff", "")
if reviewer == "quality" and "project.no-magic-discount" in request["instructions"] and "* 0.9" in diff:
    findings.append({
        "file": "src/sample/pricing.py", "side": "new", "line": 2,
        "rule": "project.no-magic-discount", "severity": "error",
        "issue": "The discount rate is a literal.", "evidence": "return subtotal * 0.9",
    })
result = {"verdict": "FAIL" if findings else "PASS", "findings": findings, "summary": "Reviewed."}
usage = {"inputTokens": len(json.dumps(request)) // 4, "outputTokens": 40}
print(json.dumps({"status": "PASSED", "summary": "reviewed", "result": result, "usage": usage}))
"""

PROJECT_REVIEW_RULE = """\
---
domain: quality
---

## project.no-magic-discount: No literal discount rates

- severity: blocking
- priority: 2
- when: a change computes a discount

Discount rates come from the pricing configuration, never from a literal in the code.
"""


def review_project(t: Transcript, flow: str, root: Path, mode: str = "find") -> list[str]:
    """A Python project with the review panel written by init, a fixture reviewer provider
    that calls no model, and a feature branch whose change uses a literal discount rate."""
    python_project(root)
    (root / "reviewer.py").write_text(REVIEWER.replace("MODE", repr(mode)))
    git(root, "add", "reviewer.py")
    git(root, "commit", "-qm", "reviewer fixture")
    here = ["--path", "."]
    t.run(flow, root, ["init", *here], 0)
    config = root / ".harness" / "project.yaml"
    config.write_text(
        config.read_text().replace("agentSandbox: enforce", "agentSandbox: 'off'")
        + "agentProviders:\n  reviewer:\n    kind: command\n    command: [python, reviewer.py]\n"
    )
    git(root, "checkout", "-q", "-b", "feat/discount")
    (root / "src" / "sample" / "pricing.py").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "    return subtotal * 0.9 if subtotal >= threshold else subtotal\n"
    )
    git(root, "commit", "-qam", "discount")
    return here


def flow_review_panel(t: Transcript, root: Path) -> None:
    """The review panel outside a run (#57) with a project rule (layer C): the rule reaches the
    quality reviewer's block, its finding fails the review (exit 6, no evidence ref); once the
    change reads the rate, the review passes, records refs/harness/review/pass/SHA and
    harness review verify checks it without calling any model."""
    flow = "review-panel"
    here = review_project(t, flow, root)
    rules = root / ".harness" / "review" / "rules"
    rules.mkdir(parents=True)
    (rules / "quality.md").write_text(PROJECT_REVIEW_RULE)
    shown = t.json(flow, root, ["review", "rules", "show", *here], 0)
    t.check(flow, shown["catalog"]["byLayer"]["C"] == 1, "the project rule is in the catalog")
    failed = t.json(flow, root, ["review-code", *here, "--provider", "reviewer"], 6)
    t.check(
        flow,
        failed["verdict"] == "FAIL"
        and [item["rule"] for item in failed["findings"]] == ["project.no-magic-discount"]
        and failed["evidenceRef"] is None,
        "the project rule's finding fails the review",
    )
    (root / "src" / "sample" / "pricing.py").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
    )
    git(root, "commit", "-qam", "read the rate")
    passed = t.json(flow, root, ["review-code", *here, "--provider", "reviewer"], 0)
    t.check(flow, passed["verdict"] == "PASS" and bool(passed["evidenceRef"]), "a pass is a ref")
    verified = t.json(flow, root, ["review", "verify", *here, "--sha", "HEAD"], 0)
    t.check(flow, verified["valid"], "the evidence verifies without models")


def flow_review_cache(t: Transcript, root: Path) -> None:
    """The same review twice (#57): the second one is a global cache hit that calls no
    reviewer; the reviewer answers are shared with another mode through the per-reviewer
    cache."""
    flow = "review-cache"
    here = review_project(t, flow, root)
    first = t.json(flow, root, ["review-code", *here, "--provider", "reviewer"], 0)
    second = t.json(flow, root, ["review-code", *here, "--provider", "reviewer"], 0)
    t.check(
        flow,
        first["cache"]["global"] == "miss"
        and second["cache"]["global"] == "hit"
        and second["tokens"]["total"] == 0
        and second["digest"] == first["digest"],
        "the second review is a cache hit with the same report digest",
    )


def flow_review_unknown(t: Transcript, root: Path) -> None:
    """A reviewer whose answer does not follow the output contract (#57) is UNKNOWN, gets one
    retry on the fallback provider and, still UNKNOWN, blocks the review (exit 6)."""
    flow = "review-unknown"
    here = review_project(t, flow, root, mode="unknown")
    report = t.json(
        flow,
        root,
        ["review-code", *here, "--provider", "reviewer", "--fallback-provider", "simulated"],
        0,
    )
    t.check(
        flow,
        report["verdict"] == "PASS"
        and all(
            item["attempts"] == 2 for item in report["reviewers"] if item["status"] != "SKIPPED"
        ),
        "the fallback provider answered the retry",
    )
    blocked = t.json(
        flow,
        root,
        [
            "review-code",
            *here,
            "--provider",
            "reviewer",
            "--fallback-provider",
            "reviewer",
            "--no-cache",
        ],
        6,
    )
    t.check(flow, blocked["verdict"] == "UNKNOWN", "a persistent UNKNOWN blocks")


def flow_review_hook(t: Transcript, root: Path) -> None:
    """The pre-push hook mode (#57): harness review hook install writes the hook; the hook mode
    fetches the base from origin (a local bare repository here) before reviewing, and aborts
    when the base cannot be fetched (exit 6)."""
    flow = "review-hook"
    here = review_project(t, flow, root)
    # The remote lives outside the workdir, whose subdirectories metrics_report.py reads.
    holder = Path(tempfile.mkdtemp(prefix="harness-remote-"))
    try:
        remote = holder / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, env=isolated_env())
        git(root, "remote", "add", "origin", str(remote))
        git(root, "push", "-q", "origin", "HEAD~1:refs/heads/main")
        installed = t.json(flow, root, ["review", "hook", "install", *here], 0)
        t.check(flow, installed["hook"].endswith("pre-push"), "the pre-push hook is installed")
        report = t.json(
            flow,
            root,
            ["review-code", *here, "--mode", "hook", "--base", "main", "--provider", "simulated"],
            0,
        )
        t.check(
            flow,
            report["baseResolution"]["fetched"]
            and report["baseResolution"]["ref"] == "origin/main",
            "hook mode fetched the base first",
        )
        git(root, "remote", "set-url", "origin", str(holder / "missing.git"))
        t.run(flow, root, ["review-code", *here, "--mode", "hook", "--base", "main"], 6)
    finally:
        shutil.rmtree(holder, ignore_errors=True)


FRICTION_AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
kind = request.get("kind", "implement")
results = {"clarify": {"questions": []}, "review": {"findings": []}, "plan": {"subtasks": []},
           "acceptance": {"tests": []}, "locate": {"locations": []},
           "architecture": {"style": "custom", "summary": "No layers.", "layers": [], "allow": {}}}
if kind == "implement":
    text = request["task"]["intent"]
    with Path("README.md").open("a", encoding="utf-8") as handle:
        handle.write("\\n" + text + "\\n")
    answer = {"status": "PASSED", "summary": "README updated"}
else:
    answer = {"status": "PASSED", "summary": kind, "result": results.get(kind, {})}
answer["usage"] = {"inputTokens": len(json.dumps(request)) // 4, "outputTokens": 20}
print(json.dumps(answer))
"""


def flow_friction(t: Transcript, root: Path) -> None:
    """Low friction for small changes and local metrics (#58): harness do on a small
    documentation change with an approval given in advance closes the run in one command
    (fast lane, the approval applied because the gate passed, no risk factor, size S); a second
    change waits in the inbox and is approved there by digest; harness metrics writes one
    self-contained HTML file. A fixture command provider writes the README; no model."""
    flow = "friction"
    python_project(root)
    (root / "README.md").write_text("# Sample\n")
    (root / "agent.py").write_text(FRICTION_AGENT)
    with (root / ".gitignore").open("a", encoding="utf-8") as handle:
        handle.write("agent.py\n*.json\nmetrics.html\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "readme")
    here = ["--path", "."]
    t.run(flow, root, ["init", *here], 0)
    import yaml  # a dependency of the package

    config_path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["agentProvider"] = "fixture"
    config["agentProviders"] = {"fixture": {"kind": "command", "command": ["python", "agent.py"]}}
    config["runtime"].update({"agentSandbox": "off", "providerRetryDelaySeconds": 0})
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    done = t.json(
        flow,
        root,
        [
            "do",
            "The discount applies at or above the threshold.",
            *here,
            "--criterion",
            "README.md states that the discount applies at or above the threshold",
            "--pre-approve",
            "--actor",
            "you",
            "--no-interactive",
            "--json",
        ],
        0,
    )
    t.check(
        flow,
        done["run"]["status"] == "PASSED"
        and done["lane"]["lane"] == "fast"
        and done["preAuthorization"]["conditions"] == ["gatePassed", "noRiskFactors", "sizeS"],
        "the fast lane closes the run with the approval given in advance",
    )
    git(root, "add", "README.md")
    git(root, "commit", "-qm", "first change")
    waiting = t.json(
        flow,
        root,
        [
            "do",
            "Amounts are rounded to two decimals.",
            *here,
            "--criterion",
            "README.md states that amounts are rounded to two decimals",
            "--no-interactive",
            "--json",
        ],
        4,
    )
    run_id = waiting["run"]["executionId"]
    inbox = t.json(flow, root, ["inbox", *here, "--json"], 0)
    pending = [item for item in inbox if item.get("executionId") == run_id]
    t.check(flow, bool(pending) and pending[0]["kind"] == "decision", "the run waits in the inbox")
    digest = pending[0]["changeSetDigest"] if pending else "sha256:missing"
    batch = t.json(
        flow,
        root,
        [
            "inbox",
            *here,
            "--approve",
            f"{run_id}={digest}",
            "--rationale",
            "Documentation only",
            "--actor",
            "you",
            "--json",
        ],
        0,
    )
    t.check(flow, batch["recorded"] == 1, "the batch decision is bound to the run's digest")
    t.run(flow, root, ["metrics", *here, "--format", "html", "--output", "metrics.html"], 0)
    page = (root / "metrics.html").read_text(encoding="utf-8")
    t.check(
        flow,
        not re.search(r"""(src|href)\s*=\s*["']?https?:""", page)
        and "<script" not in page
        and "<link" not in page
        and "<svg" in page,
        "the metrics HTML is self-contained (no remote src or href, no script)",
    )
    report = t.json(flow, root, ["metrics", *here, "--format", "json"], 0)
    t.check(
        flow,
        report["totals"]["runs"] == 2 and report["quality"]["preAuthorizedApprovals"] == 1,
        "the metrics count both runs and the approval given in advance",
    )


FLOWS = {
    "quickstart": flow_quickstart,
    "later-change": flow_later_change,
    "broken-baseline": flow_broken_baseline,
    "review-exception": flow_review_exception,
    "node": flow_node,
    "memory": flow_memory,
    "clarification": flow_clarification,
    "traceability": flow_traceability,
    "corrections": flow_corrections,
    "integrity": flow_integrity,
    "delivery": flow_delivery,
    "agent-results": flow_agent_results,
    "ladder": flow_ladder,
    "gitlab": flow_gitlab,
    "tdd": flow_tdd,
    "bdd": flow_bdd,
    "review-panel": flow_review_panel,
    "review-cache": flow_review_cache,
    "review-unknown": flow_review_unknown,
    "review-hook": flow_review_hook,
    "friction": flow_friction,
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
    # governance.chainAnchor: file (written by init) keeps chain anchors under the user's data
    # directory; the demonstration keeps them in a temporary directory of its own (not in the
    # workdir, whose subdirectories are read as projects by scripts/metrics_report.py).
    anchors = None
    if "HARNESS_ANCHOR_DIR" not in os.environ:
        anchors = tempfile.mkdtemp(prefix="harness-anchors-")
        os.environ["HARNESS_ANCHOR_DIR"] = anchors
    # runtime.stateDir: auto (written by init) keeps the run registry under the user's data
    # directory; the demonstration keeps it in a directory of its own, which metrics_report.py
    # reads when HARNESS_STATE_DIR points at it (set it before running both to keep it).
    state = None
    if "HARNESS_STATE_DIR" not in os.environ:
        state = tempfile.mkdtemp(prefix="harness-state-")
        os.environ["HARNESS_STATE_DIR"] = state
    try:
        for name in names:
            target = workdir.resolve() / name
            if target.exists():
                shutil.rmtree(target)
            FLOWS[name](transcript, target)
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)
        if anchors:
            shutil.rmtree(anchors, ignore_errors=True)
        if state:
            shutil.rmtree(state, ignore_errors=True)
    if args.transcript:
        args.transcript.write_text(json.dumps(transcript.steps, indent=2) + "\n", encoding="utf-8")
    failed = [step for step in transcript.steps if not step["ok"]]
    print(f"{len(transcript.steps)} steps, {len(failed)} unexpected exit code(s)")
    return 0 if transcript.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
