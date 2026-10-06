from __future__ import annotations

from fastapi.testclient import TestClient

from governed_harness.api import create_app

# harness init writes governance.trustedHosts: the API answers loopback host names only.
LOCAL = "http://127.0.0.1"


def test_api_health_and_empty_runs(python_workspace) -> None:
    client = TestClient(create_app(python_workspace), base_url=LOCAL)
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

    client = TestClient(create_app(python_workspace), base_url=LOCAL)
    status = client.get(f"/api/runs/{pending.execution_id}")
    assert status.status_code == 200
    assert status.json()["execution"]["currentPhase"] == "DECISION"
    evidence = client.get(f"/api/runs/{pending.execution_id}/evidence")
    assert evidence.status_code == 200
    assert evidence.json()[0]["evidence"]
    trace = client.get(f"/api/runs/{pending.execution_id}/trace?format=json")
    assert trace.status_code == 200

    refused = client.post(
        f"/api/runs/{pending.execution_id}/decision",
        json={
            "decision": "APPROVE",
            "change_set_digest": pending.change_set_digest,
            "actor_id": "agent.claude-code",
            "rationale": "self-approval",
        },
    )
    assert refused.status_code == 403
    assert "agent" in refused.json()["detail"]

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


def test_api_rejects_untrusted_host_names(python_workspace) -> None:
    """DNS rebinding: a request whose Host header is not in governance.trustedHosts is refused."""
    rebound = TestClient(create_app(python_workspace), base_url="http://attacker.example")
    assert rebound.get("/api/runs").status_code == 400
    assert (
        TestClient(create_app(python_workspace), base_url="http://localhost")
        .get("/api/runs")
        .status_code
        == 200
    )


def test_api_without_trusted_hosts_keeps_answering_every_host(python_workspace) -> None:
    config = python_workspace / ".harness" / "project.yaml"
    text = config.read_text(encoding="utf-8")
    config.write_text(text.split("governance:")[0], encoding="utf-8")
    client = TestClient(create_app(python_workspace), base_url="http://any.example")
    assert client.get("/api/runs").status_code == 200
