from __future__ import annotations

from pathlib import Path

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, ResultStatus


def test_configured_command_agent_is_a_functional_second_provider(
    python_workspace: Path, tmp_path: Path
) -> None:
    agent_script = python_workspace / "agent_adapter.py"
    agent_script.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "request = json.load(__import__('sys').stdin)\n"
        "for patch in request['task']['implementation']['patches']:\n"
        "    path = Path(patch['path'])\n"
        "    path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    operation = patch['operation']\n"
        "    content = patch.get('content') or ''\n"
        "    if operation == 'replace':\n"
        "        path.write_text(content, encoding='utf-8')\n"
        "    elif operation == 'append':\n"
        "        with path.open('a', encoding='utf-8') as handle:\n"
        "            handle.write(content)\n"
        "print(json.dumps({'status':'PASSED','summary':'Structured command adapter applied patches'}))\n",
        encoding="utf-8",
    )
    config_path = python_workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_command"
    config["agentProviders"] = {
        "fixture_command": {
            "kind": "command",
            "command": ["python", "agent_adapter.py"],
            "model": "deterministic-fixture",
        }
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    task_file = tmp_path / "command-agent-task.yaml"
    task_file.write_text(
        "title: Implement threshold discount through command adapter\n"
        "intent: Verify a provider-neutral external agent contract.\n"
        "requirements:\n"
        "  - The command adapter applies the patch and reports its status.\n"
        "acceptanceCriteria:\n"
        "  - The changed behavior passes its regression test.\n"
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
        "        def test_command_agent_threshold() -> None:\n"
        "            assert apply_discount(100, 100, 0.1) == 90\n",
        encoding="utf-8",
    )
    app = HarnessApplication()
    task = app.create_task(python_workspace, task_file)
    pending = app.start_run(python_workspace, task.task_id)
    assert pending.status is ResultStatus.BLOCKED
    assert pending.change_set_digest
    _, final = app.decide_gate(
        python_workspace,
        execution_id=pending.execution_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=pending.change_set_digest,
        actor_id="human.provider-test",
        rationale="External command provider and validators succeeded",
    )
    assert final.status is ResultStatus.PASSED
    status = app.status(python_workspace, final.execution_id)
    assert status["metrics"]["agent.invocations"]["value"] == 1
