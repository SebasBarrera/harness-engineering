from __future__ import annotations

import json
from pathlib import Path

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind


def test_trace_json_jsonl_and_sarif(python_workspace: Path, tmp_path: Path) -> None:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(
        "title: Change implementation\n"
        "intent: Add a no-op comment with a matching test change.\n"
        "acceptanceCriteria:\n  - Tests pass.\n"
        "implementation:\n"
        "  mode: patch\n"
        "  patches:\n"
        "    - path: src/sample/pricing.py\n"
        "      operation: append\n"
        "      content: |\n\n        # documented behavior\n"
        "    - path: tests/test_pricing.py\n"
        "      operation: append\n"
        "      content: |\n\n        # test suite remains authoritative\n",
        encoding="utf-8",
    )
    app = HarnessApplication()
    task = app.create_task(python_workspace, task_file)
    pending = app.start_run(python_workspace, task.task_id)
    app.decide_gate(
        python_workspace,
        execution_id=pending.execution_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=pending.change_set_digest or "",
        actor_id="human.test",
        rationale="pass",
    )
    trace_json = json.loads(app.trace(python_workspace, pending.execution_id, "json"))
    assert trace_json["execution"]["status"] == "PASSED"
    trace_jsonl = app.trace(python_workspace, pending.execution_id, "jsonl").decode()
    assert "run.created" in trace_jsonl
    sarif = json.loads(app.trace(python_workspace, pending.execution_id, "sarif"))
    assert sarif["version"] == "2.1.0"
