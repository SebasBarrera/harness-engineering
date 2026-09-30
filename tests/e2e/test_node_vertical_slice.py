from __future__ import annotations

from pathlib import Path

import pytest

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus


@pytest.mark.e2e
def test_node_profile_runs_test_lint_and_typecheck(node_workspace: Path, tmp_path: Path) -> None:
    task_file = tmp_path / "node-task.yaml"
    task_file.write_text(
        "title: Correct configuration precedence\n"
        "intent: Task values override repository and defaults.\n"
        "acceptanceCriteria:\n"
        "  - Task values override repository values.\n"
        "  - Repository values override defaults.\n"
        "implementation:\n"
        "  mode: patch\n"
        "  patches:\n"
        "    - path: src/resolve-config.js\n"
        "      operation: replace\n"
        "      content: |\n"
        "        export function resolveConfig(defaults, repository, task) {\n"
        "          return { ...defaults, ...repository, ...task };\n"
        "        }\n"
        "    - path: test/resolve-config.test.js\n"
        "      operation: append\n"
        "      content: |\n"
        "\n"
        "        test('task overrides repository', () => {\n"
        "          assert.deepEqual(resolveConfig({mode:'safe'}, {mode:'strict'}, {mode:'task'}), {mode:'task'});\n"
        "        });\n",
        encoding="utf-8",
    )
    app = HarnessApplication()
    task = app.create_task(node_workspace, task_file)
    pending = app.start_run(node_workspace, task.task_id)
    assert pending.status is ResultStatus.BLOCKED
    assert pending.current_phase is PhaseId.DECISION
    status = app.status(node_workspace, pending.execution_id)
    assert status["validationSummary"]["byStatus"] == {"PASSED": 4}
    _, final = app.decide_gate(
        node_workspace,
        execution_id=pending.execution_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=pending.change_set_digest or "",
        actor_id="human.node-reviewer",
        rationale="All Node fixture checks passed",
    )
    assert final.status is ResultStatus.PASSED
