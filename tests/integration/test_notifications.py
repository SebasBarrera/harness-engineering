"""Pending-decision inbox and webhook notifications (#53)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from test_review import start
from typer.testing import CliRunner

from governed_harness.api import create_app
from governed_harness.application import HarnessApplication, notifications
from governed_harness.cli.main import app
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.enums import DecisionKind
from governed_harness.orchestration.engine import EngineServices


class Receiver:
    """A local HTTP endpoint that records JSON bodies; the first ``failures`` calls get 500."""

    def __init__(self, failures: int = 0) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.calls = 0
        self.failures = failures
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                receiver.calls += 1
                if receiver.calls <= receiver.failures:
                    self.send_response(500)
                else:
                    receiver.bodies.append(body)
                    self.send_response(204)
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host!s}:{port}/hook"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def receiver() -> Iterator[Receiver]:
    item = Receiver()
    yield item
    item.close()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifications, "RETRY_BASE_SECONDS", 0.0)


def configure(workspace: Path, **webhook: Any) -> None:
    path = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    value["notifications"] = {"webhooks": [{"timeoutSeconds": 3, **webhook}]}
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def records(workspace: Path) -> list[dict[str, Any]]:
    services = EngineServices.open(ConfigurationResolver().resolve(workspace))
    try:
        return services.state.list_dicts("notification")
    finally:
        services.close()


def test_decision_pending_and_run_finished_are_sent_once(
    python_workspace: Path, tmp_path: Path, receiver: Receiver
) -> None:
    configure(python_workspace, url=receiver.url)
    run_id = start(python_workspace, tmp_path)
    assert [item["event"] for item in receiver.bodies] == ["decision.pending"]
    body = receiver.bodies[0]
    assert body["executionId"] == run_id and body["gateStatus"] == "PASSED"
    assert body["changeSetDigest"].startswith("sha256:")
    application = HarnessApplication()
    application.continue_run(python_workspace, run_id)
    assert len(receiver.bodies) == 1
    application.decide_gate(
        python_workspace,
        execution_id=run_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=body["changeSetDigest"],
        actor_id="reviewer",
        rationale="A rationale that must never leave the machine",
    )
    assert [item["event"] for item in receiver.bodies] == ["decision.pending", "run.finished"]
    assert receiver.bodies[1]["status"] == "PASSED"
    assert "never leave" not in json.dumps(receiver.bodies)
    stored = records(python_workspace)
    assert {item["status"] for item in stored} == {"DELIVERED"}
    assert all(receiver.url not in json.dumps(item) for item in stored)


def test_retries_then_delivers(python_workspace: Path, tmp_path: Path) -> None:
    receiver = Receiver(failures=2)
    try:
        configure(python_workspace, url=receiver.url, retries=2)
        start(python_workspace, tmp_path)
        [outcome] = records(python_workspace)
        assert outcome["status"] == "DELIVERED" and outcome["attempts"] == 3
    finally:
        receiver.close()


def test_a_failed_delivery_is_recorded_and_does_not_change_the_run(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HARNESS_TEST_HOOK", raising=False)
    configure(python_workspace, urlEnv="HARNESS_TEST_HOOK")
    run_id = start(python_workspace, tmp_path)
    status = HarnessApplication().status(python_workspace, run_id)
    assert status["execution"]["currentPhase"] == "DECISION"
    [outcome] = records(python_workspace)
    assert outcome["status"] == "FAILED" and "HARNESS_TEST_HOOK" in outcome["error"]
    assert outcome["target"] == "env:HARNESS_TEST_HOOK"


def test_url_from_the_environment(
    python_workspace: Path, tmp_path: Path, receiver: Receiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_TEST_HOOK", receiver.url)
    configure(python_workspace, urlEnv="HARNESS_TEST_HOOK", events=["decision.pending"])
    start(python_workspace, tmp_path)
    assert len(receiver.bodies) == 1


def test_exception_granted_event(
    python_workspace: Path, tmp_path: Path, receiver: Receiver
) -> None:
    configure(python_workspace, url=receiver.url, events=["exception.granted"])
    run_id = start(python_workspace, tmp_path)
    digest = HarnessApplication().review(python_workspace, run_id)["run"]["changeSetDigest"]
    HarnessApplication().decide_gate(
        python_workspace,
        execution_id=run_id,
        decision=DecisionKind.APPROVE_EXCEPTION,
        change_set_digest=digest,
        actor_id="reviewer",
        rationale="Accepted",
    )
    [body] = receiver.bodies
    assert body["event"] == "exception.granted" and body["exceptionId"].startswith("exception_")


def test_without_webhooks_nothing_is_sent_or_recorded(
    python_workspace: Path, tmp_path: Path
) -> None:
    start(python_workspace, tmp_path)
    assert records(python_workspace) == []


def test_inbox_lists_decisions_and_clarifications(python_workspace: Path, tmp_path: Path) -> None:
    run_id = start(python_workspace, tmp_path)
    vague = tmp_path / "vague.yaml"
    vague.write_text(
        "taskId: task_vague\ntitle: Vague\nintent: Make it better.\n"
        "acceptanceCriteria:\n  - It works.\n",
        encoding="utf-8",
    )
    application = HarnessApplication()
    application.create_task(python_workspace, vague)
    blocked = application.start_run(python_workspace, "task_vague")
    items = application.inbox(python_workspace)
    kinds = {item["executionId"]: item for item in items}
    assert kinds[run_id]["kind"] == "decision"
    assert kinds[run_id]["gateStatus"] == "PASSED" and kinds[run_id]["blockingFindings"] == 0
    assert kinds[blocked.execution_id]["kind"] == "clarification"
    assert kinds[blocked.execution_id]["questions"] >= 1
    text = CliRunner().invoke(app, ["--no-json", "inbox", "--path", str(python_workspace)])
    assert "decision" in text.stdout and "clarification" in text.stdout
    # harness init writes governance.trustedHosts: loopback names only.
    client = TestClient(create_app(python_workspace), base_url="http://127.0.0.1")
    assert len(client.get("/api/inbox").json()) == 2
    assert "setInterval(refresh, 5000)" in client.get("/").text
