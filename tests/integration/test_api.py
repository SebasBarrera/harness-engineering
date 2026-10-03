from __future__ import annotations

from fastapi.testclient import TestClient

from governed_harness.api import create_app


def test_api_health_and_empty_runs(python_workspace) -> None:
    client = TestClient(create_app(python_workspace))
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "PASSED"
    runs = client.get("/api/runs")
    assert runs.status_code == 200
    assert runs.json() == []
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Governed Agent Harness" in dashboard.text


def test_api_exposes_trace_evidence_retrospective_and_digest_bound_decision(
    python_workspace, tmp_path
) -> None:
    from governed_harness.application import HarnessApplication

    task_file = tmp_path / "api-task.yaml"
    task_file.write_text(
        "title: Add API fixture behavior\n"
        "intent: Exercise the application API through HTTP.\n"
        "requirements:\n"
        "  - A subtotal equal to the threshold is reduced by the rate.\n"
        "acceptanceCriteria:\n"
        "  - The threshold behavior passes.\n"
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
        "        def test_api_threshold() -> None:\n"
        "            assert apply_discount(100, 100, 0.1) == 90\n",
        encoding="utf-8",
    )
    application = HarnessApplication()
    task = application.create_task(python_workspace, task_file)
    pending = application.start_run(python_workspace, task.task_id)
    assert pending.change_set_digest

    client = TestClient(create_app(python_workspace))
    status = client.get(f"/api/runs/{pending.execution_id}")
    assert status.status_code == 200
    assert status.json()["execution"]["currentPhase"] == "DECISION"
    evidence = client.get(f"/api/runs/{pending.execution_id}/evidence")
    assert evidence.status_code == 200
    assert evidence.json()[0]["evidence"]
    trace = client.get(f"/api/runs/{pending.execution_id}/trace?format=json")
    assert trace.status_code == 200

    decision = client.post(
        f"/api/runs/{pending.execution_id}/decision",
        json={
            "decision": "APPROVE",
            "change_set_digest": pending.change_set_digest,
            "actor_id": "human.api-test",
            "rationale": "API evidence reviewed",
            "continue_after": True,
        },
    )
    assert decision.status_code == 200
    assert decision.json()["execution"]["status"] == "PASSED"
    retrospective = client.get(f"/api/runs/{pending.execution_id}/retrospective")
    assert retrospective.status_code == 200
    assert retrospective.json()["executionId"] == pending.execution_id
