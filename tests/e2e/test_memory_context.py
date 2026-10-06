from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import MemoryLevel
from governed_harness.domain.models import AgentInvocation

TASK = (
    "title: Implement threshold discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - A subtotal of 100 with a ten percent rate returns 90.\n"
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
    "        def test_at_threshold() -> None:\n"
    "            assert apply_discount(100, 100, 0.1) == 90\n"
)


def manifest_of(app: HarnessApplication, workspace: Path, execution_id: str) -> dict[str, object]:
    with app._services(workspace) as services:
        invocations = services.state.list(
            "agent_invocation", AgentInvocation, execution_id=execution_id
        )
        reference = invocations[-1].context_manifest_ref
        assert reference is not None
        manifest: dict[str, object] = json.loads(services.artifacts.get(reference))
        return manifest


@pytest.mark.e2e
def test_run_records_the_memory_it_applied_and_the_memory_it_left_out(
    python_workspace: Path, tmp_path: Path
) -> None:
    app = HarnessApplication()
    rule = app.add_memory(
        python_workspace,
        level=MemoryLevel.PROJECT,
        key="money.rounding",
        value={"text": "Round money half up to two places."},
        actor_id="human.lead",
        approved=True,
    )
    proposal = app.add_memory(
        python_workspace,
        level=MemoryLevel.PROJECT,
        key="naming",
        value={"text": "Prefer long names."},
        actor_id="human.author",
    )
    expired = app.add_memory(
        python_workspace,
        level=MemoryLevel.NORMATIVE,
        key="freeze",
        value={"text": "No changes during the freeze."},
        actor_id="human.lead",
        approved=True,
        valid_until=datetime.now(UTC) - timedelta(days=1),
    )
    secret = app.add_memory(
        python_workspace,
        level=MemoryLevel.NORMATIVE,
        key="contact",
        value={"text": "internal escalation contact"},
        actor_id="human.lead",
        approved=True,
        sensitive=True,
    )
    task_path = tmp_path / "task.yaml"
    task_path.write_text(TASK, encoding="utf-8")
    task = app.create_task(python_workspace, task_path)
    pending = app.start_run(python_workspace, task.task_id)

    manifest = manifest_of(app, python_workspace, pending.execution_id)
    records = {item["memoryId"]: item for item in manifest["records"]}  # type: ignore[union-attr]
    assert set(records) == {rule.memory_id, secret.memory_id}
    assert records[rule.memory_id]["value"] == {"text": "Round money half up to two places."}
    assert records[secret.memory_id]["value"] is None
    excluded = {item["memoryId"]: item["reason"] for item in manifest["excluded"]}  # type: ignore[union-attr]
    assert excluded == {proposal.memory_id: "unapproved", expired.memory_id: "expired"}


@pytest.mark.e2e
def test_command_provider_receives_memory_only_when_there_is_any(
    python_workspace: Path, tmp_path: Path
) -> None:
    adapter = python_workspace / "agent_adapter.py"
    adapter.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "request = json.load(sys.stdin)\n"
        "Path('request-keys.json').write_text(json.dumps(sorted(request)))\n"
        "Path('request-context.json').write_text(json.dumps(request.get('context')))\n"
        "for patch in request['task']['implementation']['patches']:\n"
        "    target = Path(patch['path'])\n"
        "    if patch['operation'] == 'replace':\n"
        "        target.write_text(patch.get('content') or '', encoding='utf-8')\n"
        "    else:\n"
        "        with target.open('a', encoding='utf-8') as handle:\n"
        "            handle.write(patch.get('content') or '')\n"
        "print(json.dumps({'status': 'PASSED', 'summary': 'applied'}))\n",
        encoding="utf-8",
    )
    config_path = python_workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_command"
    config["agentProviders"] = {
        "fixture_command": {"kind": "command", "command": ["python", "agent_adapter.py"]}
    }
    config["runtime"]["agentSandbox"] = "off"  # about memory; the sandbox has its own tests
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    task_path = tmp_path / "task.yaml"
    task_path.write_text(TASK, encoding="utf-8")
    app = HarnessApplication()

    first = app.start_run(python_workspace, app.create_task(python_workspace, task_path).task_id)
    # selfReport: harness init enables provenance.selfReport (since 2.0).
    assert json.loads((python_workspace / "request-keys.json").read_text()) == [
        "plan",
        "schemaVersion",
        "selfReport",
        "task",
    ]
    assert manifest_of(app, python_workspace, first.execution_id)["records"] == []

    rule = app.add_memory(
        python_workspace,
        level=MemoryLevel.PROJECT,
        key="money.rounding",
        value={"text": "Round money half up to two places."},
        actor_id="human.lead",
        approved=True,
    )
    second = app.start_run(python_workspace, app.create_task(python_workspace, task_path).task_id)
    context = json.loads((python_workspace / "request-context.json").read_text())
    assert [item["memoryId"] for item in context["records"]] == [rule.memory_id]
    assert context["digest"] == manifest_of(app, python_workspace, second.execution_id)["digest"]
