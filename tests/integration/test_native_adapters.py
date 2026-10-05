"""Built-in agent adapters (Claude Code, Codex, Gemini CLI, Aider) against fake CLIs that print
each agent's documented output format, and the provider environment (passEnv, env fromEnv) with
its redaction. No live agent and no network is involved."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.agents.native import render_prompt
from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import ResultStatus
from governed_harness.domain.models import AgentInvocation, ResourceUsage, ToolInvocation

TASK = (
    "title: Implement threshold discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - A subtotal of 100 with a ten percent rate returns 90.\n"
    "metadata:\n"
    "  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py]\n"
)

# The fake agent edits the owned files, writes the prompt it received next to the workspace
# (outside it, so it is not part of the ChangeSet) and prints the output of the CLI it imitates.
FAKE = """\
import json, os, sys
from pathlib import Path
argv = sys.argv[1:]
prompt = sys.stdin.read() if "--message" not in argv else argv[argv.index("--message") + 1]
Path(os.environ["FAKE_PROMPT_FILE"]).write_text(json.dumps({"argv": argv, "prompt": prompt}))
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n"
)
with open("tests/test_pricing.py", "a") as handle:
    handle.write("\\n\\ndef test_at_threshold() -> None:\\n    assert apply_discount(100, 100, 0.1) == 90\\n")
secret = os.environ.get("AGENT_API_KEY", "missing")
report = '```json\\n{"harnessSelfReport": {"assumptions": ["rates are fractions"], '\\
    '"unrequestedChanges": [{"path": "README.md", "description": "none really"}], '\\
    '"lowConfidenceAreas": [{"path": "src/sample/pricing.py", "description": "rounding"}]}}\\n```'
answer = "Done; key " + secret + "\\n" + report
OUTPUT
"""

OUTPUTS = {
    "claude-code": 'print(json.dumps({"type": "result", "subtype": "success", "is_error": False, '
    '"result": answer, "session_id": "sess-1", "total_cost_usd": 0.0421, '
    '"usage": {"input_tokens": 100, "cache_creation_input_tokens": 20, '
    '"cache_read_input_tokens": 5, "output_tokens": 40}}))',
    "codex": 'print(json.dumps({"type": "thread.started", "thread_id": "th-1"}))\n'
    'print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": answer}}))\n'
    'print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 300, '
    '"cached_input_tokens": 10, "output_tokens": 50}}))',
    "gemini-cli": 'print(json.dumps({"response": answer, "stats": {"models": {"gemini-x": '
    '{"tokens": {"prompt": 210, "candidates": 33, "total": 250, "thoughts": 7}}}}}))',
    "aider": 'print("Applied edit to src/sample/pricing.py")\n'
    'print("Tokens: 1.2k sent, 345 received. Cost: $0.0012 message, $0.0034 session.")\n'
    "print(answer)",
}

EXPECTED_USAGE = {
    "claude-code": {"input_tokens": 125, "output_tokens": 40, "cost_usd": 0.0421},
    "codex": {"input_tokens": 300, "output_tokens": 50, "cost_usd": None},
    "gemini-cli": {"input_tokens": 210, "output_tokens": 33, "cost_usd": None},
    "aider": {"input_tokens": 1200, "output_tokens": 345, "cost_usd": 0.0034},
}

SECRET = "sk-test-0123456789abcdef"


def configure(
    workspace: Path, kind: str, *, env: dict[str, Any] | None = None, output: str | None = None
) -> None:
    fake = workspace.parent / f"fake_{kind.replace('-', '_')}.py"
    fake.write_text(FAKE.replace("OUTPUT", output or OUTPUTS[kind]), encoding="utf-8")
    config_path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    provider: dict[str, Any] = {"kind": kind, "command": ["python", str(fake)], "model": "m-1"}
    provider["passEnv"] = ["FAKE_PROMPT_FILE"]
    if env is not None:
        provider["env"] = env
    config["agentProvider"] = "agent"
    config["agentProviders"] = {"agent": provider}
    config["runtime"]["agentSandbox"] = "off"  # the sandbox has its own tests
    config["provenance"] = {"selfReport": True}
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    task_path = tmp_path / "task.yaml"
    task_path.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(workspace, task_path)
    return application, application.start_run(workspace, task.task_id).execution_id


@pytest.mark.parametrize("kind", sorted(OUTPUTS))
def test_native_adapter_runs_the_cli_and_reports_usage(
    kind: str, python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt_file = tmp_path / "prompt.json"
    monkeypatch.setenv("FAKE_PROMPT_FILE", str(prompt_file))
    monkeypatch.setenv("HARNESS_TEST_AGENT_KEY", SECRET)
    configure(python_workspace, kind, env={"AGENT_API_KEY": {"fromEnv": "HARNESS_TEST_AGENT_KEY"}})
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == "DECISION", status["execution"]
    sent = json.loads(prompt_file.read_text(encoding="utf-8"))
    assert "Implement threshold discount" in sent["prompt"]
    assert "A subtotal of 100 with a ten percent rate returns 90." in sent["prompt"]
    assert "src/sample/pricing.py" in sent["prompt"]
    assert "harnessSelfReport" in sent["prompt"]
    with application._services(python_workspace) as services:
        invocation = services.state.list("agent_invocation", AgentInvocation, execution_id=run)[-1]
        usage = services.state.list("resource_usage", ResourceUsage, execution_id=run)[-1]
        tool = services.state.list("tool_invocation", ToolInvocation, execution_id=run)[0]
        stdout = services.artifacts.get(tool.stdout_ref or "")
        environment = [
            item
            for item in services.events.list(run)
            if item.event_type == "agent.environment.applied"
        ]
    assert invocation.status is ResultStatus.PASSED
    assert invocation.provider == "agent"
    assert invocation.model == "m-1"
    assert usage.quality.value == "REPORTED"
    for name, value in EXPECTED_USAGE[kind].items():
        assert getattr(usage, name) == value, name
    # The secret reached the agent (it printed it) and was redacted from the stored output.
    assert SECRET.encode() not in stdout
    assert b"<REDACTED_ENV>" in stdout
    # Only names are recorded.
    assert environment[0].payload["env"] == ["AGENT_API_KEY"]
    assert SECRET not in json.dumps(environment[0].payload)
    if kind == "aider":
        assert "<prompt>" in tool.argv
        assert not any("Implement threshold discount" in item for item in tool.argv)


def test_missing_from_env_blocks_before_the_provider_starts(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_PROMPT_FILE", str(tmp_path / "prompt.json"))
    monkeypatch.delenv("HARNESS_TEST_ABSENT", raising=False)
    configure(
        python_workspace, "claude-code", env={"AGENT_API_KEY": {"fromEnv": "HARNESS_TEST_ABSENT"}}
    )
    application, run = start(python_workspace, tmp_path)
    execution = application.status(python_workspace, run)["execution"]
    assert execution["status"] == "BLOCKED"
    assert execution["currentPhase"] == "IMPLEMENTATION"
    assert not (tmp_path / "prompt.json").exists()


def test_failed_cli_summary_carries_the_end_of_stderr(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_PROMPT_FILE", str(tmp_path / "prompt.json"))
    configure(
        python_workspace,
        "codex",
        output="sys.stderr.write('error: not logged in, run codex login\\n'); sys.exit(2)",
    )
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)
    assert status["execution"]["status"] == "FAILED"
    phases = [item for item in status["phases"] if item["phaseId"] == "IMPLEMENTATION"]
    assert "codex exited with 2: error: not logged in, run codex login" in phases[-1]["summary"]


def test_self_report_is_stored_as_reported_data_and_contrasted(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_PROMPT_FILE", str(tmp_path / "prompt.json"))
    configure(python_workspace, "claude-code")
    application, run = start(python_workspace, tmp_path)
    from governed_harness.domain.models import AgentSelfReport

    with application._services(python_workspace) as services:
        reports = services.state.list("agent_self_report", AgentSelfReport, execution_id=run)
    assert len(reports) == 1
    report = reports[0]
    assert report.quality.value == "REPORTED"
    assert report.assumptions == ("rates are fractions",)
    assert report.low_confidence_areas[0].path == "src/sample/pricing.py"
    assert report.contrast is not None
    assert report.contrast.declared_paths_not_in_change_set == ("README.md",)


def test_prompt_carries_the_feedback_block() -> None:
    request = {
        "task": {
            "task_id": "task_x",
            "title": "T",
            "intent": "I",
            "acceptance_criteria": [{"criterion_id": "AC-1", "text": "works"}],
        },
        "plan": {"steps": [{"description": "edit"}]},
        "feedback": {
            "attempt": 2,
            "trigger": "VERIFICATION_FAILED",
            "gate": {"gateId": "verification", "status": "FAILED", "reasonCodes": ["x_FAILED"]},
            "findings": [
                {
                    "severity": "HIGH",
                    "ruleId": "python.pytest.failed",
                    "location": {"path": "tests/t.py", "startLine": 3},
                    "message": "assert 1 == 2",
                }
            ],
            "validators": [
                {
                    "validatorId": "python.pytest",
                    "status": "FAILED",
                    "exitCode": 1,
                    "stdout": "E boom",
                }
            ],
            "decision": None,
        },
    }
    prompt = render_prompt(request, self_report=False)
    assert "attempt 2, VERIFICATION_FAILED" in prompt
    assert "HIGH python.pytest.failed at tests/t.py:3: assert 1 == 2" in prompt
    assert "E boom" in prompt
    assert "harnessSelfReport" not in prompt
