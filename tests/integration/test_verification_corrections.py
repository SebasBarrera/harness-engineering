"""The feedback loop around a command provider (issue #36): feedback in the request, automatic
corrections after a failed VERIFICATION, the unsupported-claim finding and transient retries."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

import governed_harness.orchestration.engine as engine_module
from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind, FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.models import (
    FEEDBACK_STREAM_CHARS,
    FEEDBACK_TOTAL_CHARS,
    AgentInvocation,
    Evidence,
    Execution,
    Finding,
)
from governed_harness.runtime.sandbox import SandboxHost
from governed_harness.storage import SQLiteStateStore

LOOP_KEYS = (
    "verificationCorrections",
    "providerFeedback",
    "unsupportedClaimSeverity",
    "providerRetries",
    "providerRetryDelaySeconds",
    "providerTransientPatterns",
)

TASK = (
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    "  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py]\n"
)

# The agent writes every request it receives to LOG, then behaves as MODE says. A buggy attempt
# leaves apply_discount unchanged, so the regression test it adds fails in VERIFICATION.
AGENT = """\
import json, sys, time
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
first = len(calls) == 1

if MODE == "transient-stderr" and first:
    sys.stderr.write("upstream error: the model is overloaded, try again later\\n")
    sys.exit(1)
if MODE == "transient-json" and first:
    print(json.dumps({"status": "FAILED", "summary": "API Error: 429 Too Many Requests"}))
    sys.exit(0)
if MODE == "hard-failure":
    sys.stderr.write("Traceback (most recent call last):\\nValueError: line 4290 is invalid\\n")
    sys.exit(1)
if MODE == "hang":
    sys.stderr.write("Request timed out\\n")
    sys.stderr.flush()
    time.sleep(30)

fixed = MODE in {"good", "transient-stderr", "transient-json"} or (
    MODE == "fix-on-feedback" and "feedback" in request
)
body = "subtotal * (1 - rate) if subtotal >= threshold else subtotal" if fixed else "subtotal"
Path("src/sample/pricing.py").write_text(
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\\n"
    f"    return {body}\\n",
    encoding="utf-8",
)
Path("tests/test_pricing.py").write_text(
    "from sample import apply_discount\\n\\n"
    "def test_at_threshold() -> None:\\n"
    "    assert apply_discount(100, 100, 0.1) == 90\\n",
    encoding="utf-8",
)
print(json.dumps({"status": "PASSED", "summary": "Implemented the threshold discount"}))
"""


def configure(
    workspace: Path, tmp_path: Path, mode: str, runtime: dict[str, Any] | None = None
) -> Path:
    """Register the fixture agent; ``runtime`` overrides keys (``None`` values remove them)."""
    log = tmp_path / f"requests-{mode}.json"
    script = AGENT.replace("LOG", repr(str(log))).replace("MODE", repr(mode))
    (workspace / "agent.py").write_text(script, encoding="utf-8")
    config_path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture_agent"
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", "agent.py"]}
    }
    config["runtime"]["providerRetryDelaySeconds"] = 0
    # About the feedback loop; the sandbox is covered below and by its own tests.
    config["runtime"]["agentSandbox"] = "off"
    for key, value in (runtime or {}).items():
        if value is None:
            config["runtime"].pop(key, None)
        else:
            config["runtime"][key] = value
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def start(workspace: Path, tmp_path: Path) -> tuple[HarnessApplication, str]:
    task_path = tmp_path / "task.yaml"
    task_path.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(workspace, task_path)
    return application, application.start_run(workspace, task.task_id).execution_id


def requests(log: Path) -> list[dict[str, Any]]:
    return list(json.loads(log.read_text(encoding="utf-8")))


def events(application: HarnessApplication, workspace: Path, run: str, kind: str) -> list[Any]:
    with application._services(workspace) as services:
        return [item for item in services.events.list(run) if item.event_type == kind]


def findings(
    application: HarnessApplication, workspace: Path, run: str, rule: str
) -> list[Finding]:
    return [item for item in application.list_findings(workspace, run) if item.rule_id == rule]


def metric(application: HarnessApplication, workspace: Path, run: str, name: str) -> Any:
    return application.status(workspace, run)["metrics"][name]["value"]


def test_failed_verification_is_corrected_with_feedback(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "fix-on-feedback")
    application, run = start(python_workspace, tmp_path)
    execution = application.status(python_workspace, run)["execution"]
    assert execution["currentPhase"] == "DECISION"
    assert application.status(python_workspace, run)["gate"]["status"] == "PASSED"

    sent = requests(log)
    assert len(sent) == 2
    assert "feedback" not in sent[0]
    feedback = sent[1]["feedback"]
    assert feedback["schemaVersion"] == "1.0"
    assert feedback["attempt"] == 2
    assert feedback["trigger"] == "VERIFICATION_FAILED"
    assert feedback["gate"] == {
        "gateId": "verification",
        "gateEvaluationId": None,
        "status": "FAILED",
        "reasonCodes": ["python.pytest_FAILED"],
    }
    assert feedback["decision"] is None
    [validator] = feedback["validators"]
    assert validator["validatorId"] == "python.pytest"
    assert validator["status"] == "FAILED"
    assert validator["exitCode"] == 1
    assert "assert" in validator["stdout"] and len(validator["stdout"]) <= FEEDBACK_STREAM_CHARS
    rules = {
        (item["ruleId"], item["severity"], item["validatorId"]) for item in feedback["findings"]
    }
    assert ("python.pytest.failed", "HIGH", "python.pytest") in rules
    assert ("agent.unsupported-claim", "MEDIUM", "harness.claim-check") in rules

    [authorized] = events(application, python_workspace, run, "correction.authorized")
    assert authorized.payload["trigger"] == "VERIFICATION_FAILED"
    assert authorized.payload["cycle"] == 1
    assert authorized.payload["maxCycles"] == 2
    assert authorized.payload["failedValidators"] == ["python.pytest"]
    assert authorized.payload["feedbackRef"].startswith("artifact://sha256/")
    assert metric(application, python_workspace, run, "correction.cycles") == 1
    assert metric(application, python_workspace, run, "correction.verification_cycles") == 1
    assert metric(application, python_workspace, run, "implementation.attempts") == 2
    assert metric(application, python_workspace, run, "agent.unsupported_claims") == 1
    assert metric(application, python_workspace, run, "agent.transient_retries") == 0

    [claim] = findings(application, python_workspace, run, "agent.unsupported-claim")
    assert claim.severity is FindingSeverity.MEDIUM
    assert "Implemented the threshold discount" in claim.message
    assert "python.pytest FAILED" in claim.message
    with application._services(python_workspace) as services:
        evidence = services.state.list("evidence", Evidence, execution_id=run)
        assert any(
            item.summary.startswith("Provider feedback for IMPLEMENTATION attempt 2")
            for item in evidence
        )
        stored = json.loads(services.artifacts.get(authorized.payload["feedbackRef"]))
    assert stored == feedback

    # The corrected candidate is approved as usual: the failed verification no longer counts.
    digest = application.status(python_workspace, run)["execution"]["changeSetDigest"]
    _, final = application.decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE,
        change_set_digest=digest,
        actor_id="human.reviewer",
        rationale="Corrected candidate passed verification",
    )
    assert final.status is ResultStatus.PASSED


def test_exhausted_corrections_stop_at_verification(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, "always-buggy")
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == "VERIFICATION"
    assert status["execution"]["status"] == "FAILED"
    assert status["gate"] is None
    assert len(requests(log)) == 3
    assert [item["feedback"]["attempt"] for item in requests(log)[1:]] == [2, 3]
    cycles = events(application, python_workspace, run, "correction.authorized")
    assert [item.payload["cycle"] for item in cycles] == [1, 2]
    [exhausted] = events(application, python_workspace, run, "correction.exhausted")
    assert exhausted.payload["cycles"] == 2 and exhausted.payload["maxCycles"] == 2
    assert metric(application, python_workspace, run, "correction.cycles") == 2
    assert metric(application, python_workspace, run, "implementation.attempts") == 3
    assert metric(application, python_workspace, run, "agent.unsupported_claims") == 3

    # Resuming does not start a fourth VERIFICATION: init writes
    # governance.applyWorkflowSettings, and the phase used its maxAttempts (3) (#51).
    resumed = application.continue_run(python_workspace, run)
    assert resumed.current_phase is PhaseId.VERIFICATION
    assert resumed.status is ResultStatus.BLOCKED
    assert "maxAttempts (3)" in (resumed.terminal_reason or "")
    assert len(requests(log)) == 3
    assert len(events(application, python_workspace, run, "correction.authorized")) == 2
    [exhausted] = events(application, python_workspace, run, "phase.attempts.exhausted")
    assert exhausted.payload == {"phaseId": "VERIFICATION", "failedAttempts": 3, "maxAttempts": 3}


def test_absent_loop_settings_keep_the_previous_behaviour(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "always-buggy", dict.fromkeys(LOOP_KEYS))
    # A 1.0.0 file has no provenance section either (selfReport adds a request key).
    config_path = python_workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.pop("provenance", None)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == "VERIFICATION"
    assert status["execution"]["status"] == "FAILED"
    [request] = requests(log)
    assert set(request) == {"schemaVersion", "task", "plan"}
    assert events(application, python_workspace, run, "correction.authorized") == []
    assert events(application, python_workspace, run, "correction.exhausted") == []
    assert findings(application, python_workspace, run, "agent.unsupported-claim") == []
    assert metric(application, python_workspace, run, "agent.unsupported_claims") == 0


def test_zero_corrections_still_record_the_unsupported_claim(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(
        python_workspace,
        tmp_path,
        "always-buggy",
        {"verificationCorrections": 0, "unsupportedClaimSeverity": "HIGH"},
    )
    application, run = start(python_workspace, tmp_path)
    assert application.status(python_workspace, run)["execution"]["status"] == "FAILED"
    assert len(requests(log)) == 1
    [claim] = findings(application, python_workspace, run, "agent.unsupported-claim")
    assert claim.severity is FindingSeverity.HIGH
    assert events(application, python_workspace, run, "correction.exhausted") == []


def test_corrections_without_feedback_repeat_the_request(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(
        python_workspace,
        tmp_path,
        "always-buggy",
        {"verificationCorrections": 1, "providerFeedback": None},
    )
    application, run = start(python_workspace, tmp_path)
    assert application.status(python_workspace, run)["execution"]["status"] == "FAILED"
    sent = requests(log)
    assert len(sent) == 2
    assert all("feedback" not in item for item in sent)
    assert sent[0]["task"] == sent[1]["task"]


def test_simulated_provider_is_not_corrected(python_workspace: Path, tmp_path: Path) -> None:
    task_path = tmp_path / "task.yaml"
    task_path.write_text(
        TASK.replace(
            "metadata:\n  ownedPaths: [src/sample/pricing.py, tests/test_pricing.py]\n", ""
        )
        + "implementation:\n"
        "  mode: patch\n"
        "  patches:\n"
        "    - path: tests/test_pricing.py\n"
        "      operation: append\n"
        "      content: |\n"
        "\n"
        "        def test_at_threshold() -> None:\n"
        "            assert apply_discount(100, 100, 0.1) == 90\n",
        encoding="utf-8",
    )
    application = HarnessApplication()
    task = application.create_task(python_workspace, task_path)
    run = application.start_run(python_workspace, task.task_id).execution_id
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == "VERIFICATION"
    assert status["execution"]["status"] == "FAILED"
    assert metric(application, python_workspace, run, "implementation.attempts") == 1
    assert events(application, python_workspace, run, "correction.authorized") == []
    assert findings(application, python_workspace, run, "agent.unsupported-claim") == []


def test_requested_changes_reach_the_agent_as_feedback(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "good")
    application, run = start(python_workspace, tmp_path)
    digest = application.status(python_workspace, run)["execution"]["changeSetDigest"]
    rationale = "Also document the threshold in the function. " * 200
    application.decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.REQUEST_CHANGES,
        change_set_digest=digest,
        actor_id="human.reviewer",
        rationale=rationale,
    )
    assert len(requests(log)) == 1
    application.continue_run(python_workspace, run)
    sent = requests(log)
    assert len(sent) == 2
    feedback = sent[1]["feedback"]
    assert feedback["trigger"] == "CHANGES_REQUESTED"
    assert feedback["attempt"] == 2
    assert feedback["gate"]["gateId"] == "delivery_candidate"
    assert feedback["gate"]["status"] == "PASSED"
    assert feedback["gate"]["reasonCodes"] == ["ALL_MANDATORY_VALIDATIONS_PASSED"]
    assert feedback["gate"]["gateEvaluationId"].startswith("gateeval")
    assert feedback["validators"] == []
    assert feedback["decision"]["decision"] == "REQUEST_CHANGES"
    assert feedback["decision"]["actorId"] == "human.reviewer"
    assert len(feedback["decision"]["rationale"]) == 4000
    assert rationale.startswith(feedback["decision"]["rationale"][:-1])


def test_feedback_validator_output_is_bounded(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, "fix-on-feedback")
    noisy = (
        "from sample import apply_discount\n\n"
        "def test_noise() -> None:\n"
        "    print('x' * 50000)\n"
        "    import sys; sys.stderr.write('y' * 50000)\n"
        "    assert False\n"
    )
    (python_workspace / "tests" / "test_noise.py").write_text(noisy, encoding="utf-8")
    application, run = start(python_workspace, tmp_path)
    # The noisy test is not owned by the task, so it keeps failing after the correction too.
    assert application.status(python_workspace, run)["execution"]["status"] == "FAILED"
    feedback = requests(log)[1]["feedback"]
    total = 0
    for validator in feedback["validators"]:
        assert len(validator["stdout"]) <= FEEDBACK_STREAM_CHARS
        assert len(validator["stderr"]) <= FEEDBACK_STREAM_CHARS
        total += len(validator["stdout"]) + len(validator["stderr"])
    assert total <= FEEDBACK_TOTAL_CHARS
    pytest_output = feedback["validators"][0]
    assert pytest_output["stdoutTruncated"] is True
    assert len(pytest_output["stdout"]) == FEEDBACK_STREAM_CHARS


@pytest.mark.parametrize(
    ("mode", "pattern"), [("transient-stderr", "overloaded"), ("transient-json", "429")]
)
def test_transient_provider_failure_is_retried(
    python_workspace: Path, tmp_path: Path, mode: str, pattern: str
) -> None:
    log = configure(python_workspace, tmp_path, mode)
    application, run = start(python_workspace, tmp_path)
    assert application.status(python_workspace, run)["execution"]["currentPhase"] == "DECISION"
    sent = requests(log)
    assert len(sent) == 2 and sent[0] == sent[1]
    [retried] = events(application, python_workspace, run, "agent.invocation.retried")
    assert retried.payload["matchedPattern"] == pattern
    assert retried.payload["retry"] == 1 and retried.payload["maxRetries"] == 3
    assert metric(application, python_workspace, run, "agent.transient_retries") == 1
    assert metric(application, python_workspace, run, "agent.invocations") == 2
    assert metric(application, python_workspace, run, "correction.cycles") == 0
    assert metric(application, python_workspace, run, "implementation.attempts") == 1
    with application._services(python_workspace) as services:
        invocations = services.state.list("agent_invocation", AgentInvocation, execution_id=run)
        evidence = services.state.list("evidence", Evidence, execution_id=run)
    assert sorted(item.status for item in invocations) == sorted(
        [ResultStatus.PASSED, ResultStatus.FAILED]
    )
    assert any(item.summary.startswith("Transient provider failure") for item in evidence)


def test_non_transient_failure_is_not_retried(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(python_workspace, tmp_path, "hard-failure")
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == "IMPLEMENTATION"
    assert status["execution"]["status"] == "FAILED"
    assert len(requests(log)) == 1
    assert events(application, python_workspace, run, "agent.invocation.retried") == []


def test_provider_killed_at_its_timeout_is_not_retried(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(python_workspace, tmp_path, "hang", {"commandTimeoutSeconds": 1})
    application, run = start(python_workspace, tmp_path)
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == "IMPLEMENTATION"
    assert status["execution"]["status"] == "TIMED_OUT"
    assert len(requests(log)) == 1
    assert events(application, python_workspace, run, "agent.invocation.retried") == []


def test_retries_are_bounded(python_workspace: Path, tmp_path: Path) -> None:
    log = configure(
        python_workspace,
        tmp_path,
        "hard-failure",
        {"providerRetries": 2, "providerTransientPatterns": ["ValueError"]},
    )
    application, run = start(python_workspace, tmp_path)
    assert application.status(python_workspace, run)["execution"]["status"] == "FAILED"
    assert len(requests(log)) == 3
    assert metric(application, python_workspace, run, "agent.transient_retries") == 2


class FakeClock:
    """Replaces the engine's clock: sleeping advances it instead of waiting."""

    def __init__(self, on_sleep: Callable[[], None] | None = None) -> None:
        self.now = 0.0
        self.slept: list[float] = []
        self.on_sleep = on_sleep

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


def test_retry_waits_the_configured_delay(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(engine_module, "time", clock)
    configure(python_workspace, tmp_path, "transient-stderr", {"providerRetryDelaySeconds": 60})
    application, run = start(python_workspace, tmp_path)
    assert application.status(python_workspace, run)["execution"]["currentPhase"] == "DECISION"
    assert sum(clock.slept) == 60
    [retried] = events(application, python_workspace, run, "agent.invocation.retried")
    assert retried.payload["delaySeconds"] == 60


def test_cancellation_during_the_retry_delay_stops_the_run(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cancel_every_run() -> None:
        with SQLiteStateStore(python_workspace / ".harness" / "state.db") as state:
            for execution in state.list("execution", Execution):
                state.set_flag(f"cancel:{execution.execution_id}", "1")

    monkeypatch.setattr(engine_module, "time", FakeClock(cancel_every_run))
    log = configure(
        python_workspace, tmp_path, "transient-stderr", {"providerRetryDelaySeconds": 60}
    )
    application, run = start(python_workspace, tmp_path)
    assert application.status(python_workspace, run)["execution"]["status"] == "CANCELLED"
    assert len(requests(log)) == 1


@pytest.mark.skipif(os.name == "nt", reason="the stand-in wrapper is a POSIX shell script")
@pytest.mark.parametrize(
    ("mode", "sandboxed_phases"), [("fix-on-feedback", 2), ("transient-stderr", 1)]
)
def test_corrections_and_retries_run_under_the_agent_sandbox(
    python_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    sandboxed_phases: int,
) -> None:
    """Every provider call of the loop, a correction attempt or a repeated call, goes through
    the same sandbox prefix as the first call. The host is injected, so this holds on hosts
    without a mechanism too."""
    wrapper_log = tmp_path / "wrapper.log"
    wrapper = tmp_path / "fake-sandbox-exec"
    wrapper.write_text(f'#!/bin/sh\necho "$1" >> "{wrapper_log}"\nshift 2\nexec "$@"\n')
    wrapper.chmod(0o755)
    host = SandboxHost(system="Darwin", home=tmp_path, temp_dir=tmp_path, sandbox_exec=str(wrapper))
    monkeypatch.setattr(SandboxHost, "detect", classmethod(lambda cls: host))
    log = configure(python_workspace, tmp_path, mode, {"agentSandbox": "enforce"})
    application, run = start(python_workspace, tmp_path)
    assert application.status(python_workspace, run)["execution"]["currentPhase"] == "DECISION"
    assert len(requests(log)) == 2
    assert wrapper_log.read_text().splitlines() == ["-p", "-p"]
    applied = events(application, python_workspace, run, "agent.sandbox.applied")
    assert len(applied) == sandboxed_phases
