#!/usr/bin/env python3
"""Reproduce the demonstration flows of the thesis with the installed ``harness`` CLI.

Every flow runs in a fresh temporary Git repository with the deterministic ``simulated``
provider, and every command's exit code is checked against the documented expectation.
The script is used by CI in three ways:

* ``quickstart``: the README quickstart, command for command (docs-smoke workflow);
* ``all``: quickstart plus the later-change, broken-baseline, review-exception, Node.js, memory
  and clarification flows, leaving the projects in ``--workdir`` so
  ``scripts/metrics_report.py`` can read them;
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

    def check(self, flow: str, condition: bool, description: str) -> None:
        """Record an assertion on the output of a step, next to its exit code."""
        self.ok &= condition
        mark = "ok " if condition else "BAD"
        print(f"[{mark}] {flow:<14} check: {description}")


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

    # A command provider that reports its usage fills the token and cost metrics.
    (root / "usage_adapter.py").write_text(USAGE_ADAPTER)
    config = root / ".harness" / "project.yaml"
    config.write_text(
        config.read_text()
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


FLOWS = {
    "quickstart": flow_quickstart,
    "later-change": flow_later_change,
    "broken-baseline": flow_broken_baseline,
    "review-exception": flow_review_exception,
    "node": flow_node,
    "memory": flow_memory,
    "clarification": flow_clarification,
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
