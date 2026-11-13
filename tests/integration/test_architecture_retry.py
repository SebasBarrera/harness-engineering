"""A malformed architecture answer is retried once and told why (2.1).

In the Haiku greenfield campaign the advice came back without an option's ``supports`` and
INTENT stopped on ``architecture.malformed``. Under ``runtime.contractRetry`` the call is sent
once more; ``explainProblem`` (which ``harness init`` writes) ends the second request with the
validation error, so the agent can correct it. Two malformed answers still block, and without
``explainProblem`` the second request is the first one again."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from governed_harness.application import HarnessApplication
from tests.integration.architecture_agent import install
from tests.integration.test_engineering_flow import (
    CORE,
    CORE_TEST,
    NEW_TASK,
    _new_project,
    calls,
    create,
    events,
    findings,
)

FILES = {
    "src/app/core/__init__.py": "",
    "src/app/core/cart.py": CORE,
    "tests/test_cart.py": CORE_TEST,
}


def _run(tmp_path: Path, variant: str, retry: dict[str, Any] | None) -> tuple[Path, Path, Any]:
    root = _new_project(tmp_path / "new")
    sections: dict[str, Any] = {"architecture": {"mode": "agent", "catalogue": "experts"}}
    if retry is not None:
        sections["runtime"] = {"contractRetry": retry}
    log = install(root, tmp_path, FILES, sections, variant)
    task = create(root, tmp_path, list(FILES), NEW_TASK)
    return root, log, HarnessApplication().start_run(root, task)


def _advice(log: Path) -> list[dict[str, Any]]:
    return [item for item in calls(log, "architecture") if item.get("mode") == "advise"]


def _retries(root: Path, run: str) -> list[dict[str, Any]]:
    return [
        item["payload"]
        for item in events(root, run)
        if item["eventType"] == "agent.call.contract-retry"
    ]


def _malformed(root: Path, run: str) -> list[str]:
    return [
        item.rule_id for item in findings(root, run) if item.rule_id == "architecture.malformed"
    ]


def test_a_malformed_advice_is_retried_with_the_error_and_the_flow_goes_on(
    tmp_path: Path,
) -> None:
    root, log, run = _run(tmp_path, "no-supports-once", {"mode": "once", "explainProblem": True})
    first, second = _advice(log)
    assert "rejected" not in first["instructions"]
    assert "Your previous answer to this request was rejected" in second["instructions"]
    assert "needs a 'fit' reason and 'supports'" in second["instructions"]
    [retry] = _retries(root, run.execution_id)
    assert retry["callKind"] == "architecture"
    assert retry["problemSentToAgent"] is True
    assert "needs a 'fit' reason and 'supports'" in retry["problem"]
    assert _malformed(root, run.execution_id) == []
    assert HarnessApplication().architecture(root)["state"]["status"] == "OPTIONS"


def test_two_malformed_advices_block_as_before(tmp_path: Path) -> None:
    root, log, run = _run(tmp_path, "no-supports", {"mode": "once", "explainProblem": True})
    assert len(_advice(log)) == 2
    assert (run.current_phase.value, run.status.value) == ("INTENT", "BLOCKED")
    assert HarnessApplication().architecture(root)["state"] is None
    assert _malformed(root, run.execution_id) == ["architecture.malformed"]


def test_without_explain_problem_the_second_request_is_the_first_one(tmp_path: Path) -> None:
    root, log, run = _run(tmp_path, "no-supports-once", {"mode": "once"})
    first, second = _advice(log)
    assert second["instructions"] == first["instructions"]
    [retry] = _retries(root, run.execution_id)
    assert "problemSentToAgent" not in retry


def test_without_contract_retry_a_malformed_advice_blocks_at_once(tmp_path: Path) -> None:
    root, log, run = _run(tmp_path, "no-supports-once", None)
    assert len(_advice(log)) == 1
    assert (run.current_phase.value, run.status.value) == ("INTENT", "BLOCKED")
    assert _retries(root, run.execution_id) == []
    assert _malformed(root, run.execution_id) == ["architecture.malformed"]
