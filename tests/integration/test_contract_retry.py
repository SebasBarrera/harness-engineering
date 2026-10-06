"""A read-only call whose answer breaks its contract (#80).

In the 2.0.0 pilot an acceptance-test call answered with invalid JSON and SPECIFICATION blocked
at once, while the review panel retries an invalid answer once on its fallback provider. Under
``runtime.contractRetry`` every read-only call (clarify, plan, acceptance, locate, architecture,
review) gets that second attempt, on ``fallbackProvider`` when configured, and both attempts are
recorded; without the key the first broken answer still blocks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import PhaseId, ResultStatus
from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.models import AgentInvocation

TASK = (
    "taskId: task_retry\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    "  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py, tests/acceptance/test_ac_1.py]\n"
)

# BROKEN: how many of this provider's read-only answers break their contract before it answers
# well; SHAPE: "json" prints something that is not JSON, "result" a result the phase rejects.
AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append({"name": NAME, **request})
log.write_text(json.dumps(calls))
kind = request.get("kind", "implement")
mine = [item for item in calls if item["name"] == NAME and item.get("kind") == kind]
if kind in {"acceptance", "clarify"}:
    if len(mine) <= BROKEN:
        if SHAPE == "json":
            print("Here are the tests you asked for: {not json")
        else:
            print(json.dumps({"status": "PASSED", "summary": "x", "result": {"tests": "none",
                                                                          "questions": "none"}}))
        sys.exit(0)
    content = (
        "from sample import apply_discount\\n\\n\\n"
        "def test_ac_1() -> None:\\n    assert apply_discount(100, 100, 0.1) == 90\\n"
    )
    result = {"tests": [{"path": "tests/acceptance/test_ac_1.py", "content": content}],
              "questions": []}
    print(json.dumps({"status": "PASSED", "summary": "answered", "result": result}))
    sys.exit(0)
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\\n"
)
print(json.dumps({"status": "PASSED", "summary": "implemented"}))
"""


def script(workspace: Path, log: Path, name: str, broken: int, shape: str) -> list[str]:
    path = workspace / f"{name}.py"
    path.write_text(
        AGENT.replace("LOG", repr(str(log)))
        .replace("NAME", repr(name))
        .replace("BROKEN", str(broken))
        .replace("SHAPE", repr(shape)),
        encoding="utf-8",
    )
    return ["python", path.name]


def configure(
    workspace: Path,
    tmp_path: Path,
    *,
    broken: int,
    shape: str = "json",
    retry: dict[str, Any] | None = None,
    fallback_broken: int | None = None,
    intake: dict[str, Any] | None = None,
) -> Path:
    log = tmp_path / "calls.json"
    providers = {
        "fixture_agent": {
            "kind": "command",
            "command": script(workspace, log, "fixture_agent", broken, shape),
        }
    }
    if fallback_broken is not None:
        providers["second_agent"] = {
            "kind": "command",
            "command": script(workspace, log, "second_agent", fallback_broken, shape),
        }
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = providers
    config["runtime"].update(
        {"agentSandbox": "off", "providerRetries": 0, "verificationCorrections": 0}
    )
    if retry is not None:
        config["runtime"]["contractRetry"] = retry
    config["verification"] = {
        "requirementTraceability": "off",
        "acceptanceTests": {"mode": "agent"},
    }
    if intake is not None:
        config["intake"] = intake
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, source)
    return application, application.start_run(workspace, "task_retry").execution_id


def calls_of(log: Path, kind: str) -> list[dict[str, Any]]:
    return [item for item in json.loads(log.read_text()) if item.get("kind") == kind]


def retries(application: HarnessApplication, workspace: Path, run: str) -> list[dict[str, Any]]:
    with application._services(workspace) as services:
        return [
            item.payload
            for item in services.events.list(run)
            if item.event_type == "agent.call.contract-retry"
        ]


def invocations(application: HarnessApplication, workspace: Path, run: str, kind: str) -> list[Any]:
    with application._services(workspace) as services:
        return [
            item
            for item in services.state.list("agent_invocation", AgentInvocation, execution_id=run)
            if item.call_kind == kind
        ]


def test_without_the_key_a_broken_answer_blocks_at_once(
    python_workspace: Path, tmp_path: Path
) -> None:
    """The defect of the pilot: invalid JSON of the acceptance call blocks SPECIFICATION."""
    log = configure(python_workspace, tmp_path, broken=1)
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)["execution"]
    assert status["currentPhase"] == PhaseId.SPECIFICATION
    assert status["status"] == ResultStatus.BLOCKED
    assert len(calls_of(log, "acceptance")) == 1
    assert retries(application, python_workspace, run) == []


def test_invalid_json_is_retried_once_and_both_attempts_are_recorded(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, broken=1, retry={"mode": "once"})
    application, run = start(python_workspace, tmp_path)
    assert len(calls_of(log, "acceptance")) == 2
    # The second answer was valid: the tests wait for a person, as after a good first answer.
    proposal = application.acceptance(python_workspace, run)
    assert proposal["status"] == "PROPOSED"
    [retry] = retries(application, python_workspace, run)
    assert (retry["callKind"], retry["provider"], retry["retryProvider"]) == (
        "acceptance",
        "fixture_agent",
        "fixture_agent",
    )
    assert retry["attempt"] == 1
    attempts = invocations(application, python_workspace, run, "acceptance")
    assert [item.status for item in attempts] == [ResultStatus.ERROR, ResultStatus.PASSED]
    assert retry["invocationId"] == attempts[0].invocation_id


def test_the_retry_goes_to_the_fallback_provider(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(
        python_workspace,
        tmp_path,
        broken=5,
        shape="result",
        retry={"mode": "once", "fallbackProvider": "second_agent"},
        fallback_broken=0,
    )
    application, run = start(python_workspace, tmp_path)
    names = [item["name"] for item in calls_of(log, "acceptance")]
    assert names == ["fixture_agent", "second_agent"]
    [retry] = retries(application, python_workspace, run)
    assert retry["retryProvider"] == "second_agent"
    assert "tests" in retry["problem"]
    assert application.acceptance(python_workspace, run)["status"] == "PROPOSED"


def test_two_broken_answers_block(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, broken=2, shape="result", retry={"mode": "once"})
    application, run = start(python_workspace, tmp_path)
    assert len(calls_of(log, "acceptance")) == 2
    status = application.status(python_workspace, run)["execution"]
    assert status["status"] == ResultStatus.BLOCKED
    malformed = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "acceptance.malformed"
    ]
    assert len(malformed) == 1


def test_a_clarify_answer_is_retried_too(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(
        python_workspace,
        tmp_path,
        broken=1,
        shape="result",
        retry={"mode": "once"},
        intake={"criteriaPolicy": "enforce", "ambiguityReview": "agent"},
    )
    application, run = start(python_workspace, tmp_path)
    assert len(calls_of(log, "clarify")) == 2
    kinds = [item["callKind"] for item in retries(application, python_workspace, run)]
    # The fixture also breaks its first acceptance answer.
    assert kinds == ["clarify", "acceptance"]
    assert application.status(python_workspace, run)["execution"]["currentPhase"] != (
        PhaseId.INTENT
    )


def test_an_unknown_fallback_provider_is_a_configuration_error(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(
        python_workspace,
        tmp_path,
        broken=0,
        retry={"mode": "once", "fallbackProvider": "nowhere"},
    )
    app = HarnessApplication()
    with pytest.raises(ConfigurationError, match="fallbackProvider"):
        app.validate_config(python_workspace)
