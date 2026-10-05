"""Decision brief, run references, artifact show and the interactive decision (#53)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from governed_harness.api import create_app
from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.domain.enums import DecisionKind

TASK = """\
taskId: {task_id}
title: Apply a discount at the threshold
intent: Apply a percentage discount only when the subtotal reaches the threshold.
requirements:
  - requirementId: req_discount
    text: A subtotal at or above the threshold is reduced by the rate.
acceptanceCriteria:
  - criterionId: ac_at_threshold
    text: A subtotal of 100 with threshold 100 and rate 0.1 returns 90.
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


        def test_req_discount_at_threshold() -> None:
            assert apply_discount(100, 100, 0.1) == 90
"""


def start(workspace: Path, tmp_path: Path, task_id: str = "task_review") -> str:
    task_file = tmp_path / f"{task_id}.yaml"
    task_file.write_text(TASK.format(task_id=task_id), encoding="utf-8")
    application = HarnessApplication()
    application.create_task(workspace, task_file)
    return application.start_run(workspace, task_id).execution_id


def invoke(workspace: Path, *args: str, stdin: str | None = None) -> tuple[int, str, str]:
    result = CliRunner().invoke(app, [*args, "--path", str(workspace)], input=stdin)
    return result.exit_code, result.stdout, result.stderr


def test_brief_of_a_run_waiting_for_a_decision(python_workspace: Path, tmp_path: Path) -> None:
    run_id = start(python_workspace, tmp_path)
    brief = HarnessApplication().review(python_workspace, run_id)
    assert brief["run"]["awaitingDecision"] is True
    digest = brief["run"]["changeSetDigest"]
    assert brief["asked"]["acceptanceCriteria"][0]["criterionId"] == "ac_at_threshold"
    assert {item["path"] for item in brief["changed"]["files"]} == {
        "src/sample/pricing.py",
        "tests/test_pricing.py",
    }
    assert brief["changed"]["totals"]["files"] == 2
    assert brief["gate"]["status"] == "PASSED"
    assert brief["gate"]["reasons"][0]["explanation"].startswith("every mandatory validator")
    assert brief["verified"]["changeSetDigest"] == digest
    assert any(
        item["validatorId"] == "python.pytest" and item["status"] == "PASSED"
        for item in brief["verified"]["validations"]
    )
    assert brief["verified"]["requirements"] == [
        {
            "requirementId": "req_discount",
            "traced": True,
            "tests": ["tests/test_pricing.py::test_req_discount_at_threshold"],
        }
    ]
    assert any("not a review by another person" in item for item in brief["notVerified"])
    assert brief["delta"] is None
    assert digest in brief["next"][1]


def test_run_references(python_workspace: Path, tmp_path: Path) -> None:
    first = start(python_workspace, tmp_path, "task_first")
    second = start(python_workspace, tmp_path, "task_second")
    application = HarnessApplication()
    assert application.resolve_run(python_workspace, "latest") == second
    assert application.resolve_run(python_workspace, first[4:12]) == first
    assert application.resolve_run(python_workspace, first[:12]) == first
    code, _, err = invoke(python_workspace, "status", "--run", "run_")
    assert code == 2 and "matches 2 runs" in json.loads(err)["error"]
    code, out, _ = invoke(python_workspace, "findings", "list")
    assert code == 0 and isinstance(json.loads(out), list)
    code, out, _ = invoke(python_workspace, "status")
    assert json.loads(out)["execution"]["executionId"] == second


def test_review_cli_and_artifact_show(python_workspace: Path, tmp_path: Path) -> None:
    run_id = start(python_workspace, tmp_path)
    code, out, _ = invoke(python_workspace, "review", "--run", run_id, "--diff")
    assert code == 0
    brief = json.loads(out)
    assert brief["changed"]["diff"].startswith("--- a/")
    text = CliRunner().invoke(app, ["--no-json", "review", "--path", str(python_workspace)])
    assert "Decision brief - run" in text.stdout and "What changed (2 file(s)" in text.stdout
    reference = brief["changed"]["diffRef"]
    code, out, _ = invoke(python_workspace, "artifact", "show", reference)
    assert code == 0 and out == brief["changed"]["diff"]
    hex_digest = reference.removeprefix("artifact://sha256/")
    code, out, _ = invoke(python_workspace, "artifact", "show", hex_digest[:10], "--describe")
    described = json.loads(out)
    assert described["verified"] is True and described["mediaType"] == "text/x-diff"
    code, _, err = invoke(python_workspace, "artifact", "show", "0000000")
    assert code == 3 and "hint" in json.loads(err)
    code, _, _ = invoke(python_workspace, "artifact", "show", "xyz")
    assert code == 2


def test_decide_without_options_needs_a_terminal(python_workspace: Path, tmp_path: Path) -> None:
    run_id = start(python_workspace, tmp_path)
    code, _, err = invoke(python_workspace, "gate", "decide", "--run", run_id)
    assert code == 2
    assert "--interactive" in json.loads(err)["hint"]


def test_interactive_decision_confirms_the_digest(python_workspace: Path, tmp_path: Path) -> None:
    run_id = start(python_workspace, tmp_path)
    digest = HarnessApplication().review(python_workspace, run_id)["run"]["changeSetDigest"]
    wrong = "APPROVE\nLooks right\n000000000000\n"
    code, out, _ = invoke(
        python_workspace, "gate", "decide", "--run", run_id, "--interactive", stdin=wrong
    )
    assert code == 5 and "Decision brief" in out
    assert HarnessApplication().status(python_workspace, run_id)["humanDecision"] is None
    right = f"APPROVE\nCriteria covered by tests\n{digest[7:19]}\n"
    code, out, _ = invoke(
        python_workspace, "gate", "decide", "--run", "latest", "-i", "--actor", "you", stdin=right
    )
    assert code == 0, out
    status = HarnessApplication().status(python_workspace, run_id)
    assert status["execution"]["status"] == "PASSED"
    assert status["humanDecision"]["changeSetDigest"] == digest


def test_wrong_digest_hint_shows_the_current_digest(python_workspace: Path, tmp_path: Path) -> None:
    run_id = start(python_workspace, tmp_path)
    digest = HarnessApplication().review(python_workspace, run_id)["run"]["changeSetDigest"]
    code, _, err = invoke(
        python_workspace,
        "gate",
        "decide",
        "--run",
        run_id,
        "--decision",
        "APPROVE",
        "--change-set-digest",
        "sha256:abc",
        "--rationale",
        "x",
    )
    assert code == 5
    hint = json.loads(err)["hint"]
    assert digest in hint and "No decision has been recorded" in hint


def test_delta_since_a_requested_change(python_workspace: Path, tmp_path: Path) -> None:
    run_id = start(python_workspace, tmp_path)
    application = HarnessApplication()
    first = application.review(python_workspace, run_id)["run"]["changeSetDigest"]
    application.decide_gate(
        python_workspace,
        execution_id=run_id,
        decision=DecisionKind.REQUEST_CHANGES,
        change_set_digest=first,
        actor_id="reviewer",
        rationale="Add a test below the threshold",
    )
    # The simulated provider applies the patch again: the append patch grows the test file.
    application.continue_run(python_workspace, run_id)
    brief = application.review(python_workspace, run_id)
    assert brief["decisions"][0]["decision"] == "REQUEST_CHANGES"
    assert brief["history"]["requestedChanges"] == 1
    assert brief["history"]["implementationAttempts"] == 2
    assert brief["run"]["changeSetDigest"] != first
    assert brief["delta"]["unchanged"] is False
    assert brief["delta"]["sinceDecision"]["changeSetDigest"] == first
    assert brief["delta"]["files"] == {
        "added": [],
        "removed": [],
        "changed": ["tests/test_pricing.py"],
    }
    application.decide_gate(
        python_workspace,
        execution_id=run_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=brief["run"]["changeSetDigest"],
        actor_id="reviewer",
        rationale="Covered",
    )
    closed = application.review(python_workspace, run_id)
    assert closed["delta"]["unchanged"] is True
    assert closed["run"]["awaitingDecision"] is False


def test_api_review_and_dashboard_request_changes(python_workspace: Path, tmp_path: Path) -> None:
    run_id = start(python_workspace, tmp_path)
    client = TestClient(create_app(python_workspace))
    review = client.get(f"/api/runs/{run_id}/review")
    assert review.status_code == 200
    assert review.json()["run"]["awaitingDecision"] is True
    assert client.get("/api/runs/latest/review").json()["run"]["executionId"] == run_id
    page = client.get("/").text
    assert 'data-decision="REQUEST_CHANGES"' in page
    digest = review.json()["run"]["changeSetDigest"]
    decided = client.post(
        f"/api/runs/{run_id}/decision",
        json={
            "decision": "REQUEST_CHANGES",
            "change_set_digest": digest,
            "actor_id": "human.web",
            "rationale": "Cover the value below the threshold",
        },
    )
    assert decided.status_code == 200
    assert decided.json()["decision"]["decision"] == "REQUEST_CHANGES"
