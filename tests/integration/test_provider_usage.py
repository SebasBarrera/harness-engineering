from __future__ import annotations

from pathlib import Path

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import ResultStatus
from governed_harness.domain.models import AgentInvocation, ResourceUsage

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

ADAPTER = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "request = json.load(sys.stdin)\n"
    "for patch in request['task']['implementation']['patches']:\n"
    "    target = Path(patch['path'])\n"
    "    if patch['operation'] == 'replace':\n"
    "        target.write_text(patch.get('content') or '', encoding='utf-8')\n"
    "    else:\n"
    "        with target.open('a', encoding='utf-8') as handle:\n"
    "            handle.write(patch.get('content') or '')\n"
    "print(json.dumps({'status': 'PASSED', 'summary': 'applied'__USAGE__}))\n"
)


def run_with(workspace: Path, tmp_path: Path, usage: str) -> tuple[HarnessApplication, str]:
    (workspace / "agent_adapter.py").write_text(
        ADAPTER.replace("__USAGE__", usage), encoding="utf-8"
    )
    config_path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_command"
    config["agentProviders"] = {
        "fixture_command": {"kind": "command", "command": ["python", "agent_adapter.py"]}
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    task_path = tmp_path / "task.yaml"
    task_path.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(workspace, task_path)
    return application, application.start_run(workspace, task.task_id).execution_id


def test_reported_usage_becomes_token_and_cost_metrics(
    python_workspace: Path, tmp_path: Path
) -> None:
    application, run = run_with(
        python_workspace,
        tmp_path,
        ", 'usage': {'inputTokens': 1200, 'outputTokens': 340, 'costUsd': 0.0125}",
    )
    metrics = application.status(python_workspace, run)["metrics"]
    assert metrics["tokens.input"]["value"] == 1200
    assert metrics["tokens.input"]["quality"] == "REPORTED"
    assert metrics["tokens.output"]["value"] == 340
    assert metrics["cost.usd"]["value"] == 0.0125
    assert metrics["cost.usd"]["quality"] == "REPORTED"
    assert metrics["tokens.reasoning"]["quality"] == "NOT_AVAILABLE"
    with application._services(python_workspace) as services:
        invocation = services.state.list("agent_invocation", AgentInvocation, execution_id=run)[-1]
        usage = services.state.list("resource_usage", ResourceUsage, execution_id=run)
        assert invocation.usage_ref is not None
        assert services.artifacts.verify(invocation.usage_ref)
    assert [item.invocation_id for item in usage] == [invocation.invocation_id]
    assert usage[0].quality.value == "REPORTED"


def test_missing_usage_stays_not_available(python_workspace: Path, tmp_path: Path) -> None:
    application, run = run_with(python_workspace, tmp_path, "")
    metrics = application.status(python_workspace, run)["metrics"]
    assert metrics["tokens.input"]["value"] is None
    assert metrics["tokens.input"]["quality"] == "NOT_AVAILABLE"
    assert metrics["cost.usd"]["quality"] == "NOT_AVAILABLE"


def test_malformed_usage_is_a_protocol_error(python_workspace: Path, tmp_path: Path) -> None:
    """A malformed report closes the step instead of being ignored or estimated."""
    application, run = run_with(python_workspace, tmp_path, ", 'usage': {'inputTokens': -5}")
    status = application.status(python_workspace, run)
    assert status["execution"]["status"] == ResultStatus.ERROR.value
    assert status["metrics"]["tokens.input"]["quality"] == "NOT_AVAILABLE"
    with application._services(python_workspace) as services:
        invocation = services.state.list("agent_invocation", AgentInvocation, execution_id=run)[-1]
    assert invocation.error is not None
    assert invocation.error.kind.value == "PROTOCOL_ERROR"
    assert invocation.usage_ref is None
