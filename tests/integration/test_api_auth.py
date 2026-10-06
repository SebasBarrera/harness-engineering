"""Authentication, roles and the decision audit of the local API (#18).

Tokens are built at run time (never a literal that looks like a secret) and only reach the
server through the environment mapping passed to ``create_app`` or the start token."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from governed_harness.api import create_app
from governed_harness.api.auth import AUDIT_LOG
from governed_harness.application import HarnessApplication
from governed_harness.domain.errors import ConfigurationError

cli = importlib.import_module("governed_harness.cli.main")

LOCAL = "http://127.0.0.1"

TASK = (
    "title: Discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n  - A subtotal of 100 at ten percent returns 90.\n"
    "implementation:\n  mode: patch\n  patches:\n"
    "    - path: src/sample/pricing.py\n      operation: replace\n      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
    "    - path: tests/test_pricing.py\n      operation: append\n      content: |\n"
    "\n"
    "        def test_api_threshold() -> None:\n"
    "            assert apply_discount(100, 100, 0.1) == 90\n"
)


def _token(name: str) -> str:
    return f"tok-{name}-" + "x" * 24


ADMIN, REVIEWER, VIEWER = _token("admin"), _token("reviewer"), _token("viewer")
ENVIRON = {"TEST_API_REVIEWER": REVIEWER, "TEST_API_VIEWER": VIEWER}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _write_api(workspace: Path, section: dict[str, Any] | None) -> None:
    config = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(config.read_text(encoding="utf-8"))
    if section is None:
        value.pop("api", None)
    else:
        value["api"] = section
    config.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


SECTION: dict[str, Any] = {
    "auth": "token",
    "tokenEnv": "TEST_API_START",
    "tokenUser": "human.owner",
    "tokenRole": "admin",
    "users": [
        {"id": "alice", "role": "reviewer", "tokenEnv": "TEST_API_REVIEWER"},
        {"id": "bob", "role": "viewer", "tokenEnv": "TEST_API_VIEWER"},
    ],
}


@pytest.fixture
def secured(python_workspace: Path) -> TestClient:
    _write_api(python_workspace, SECTION)
    return TestClient(
        create_app(python_workspace, start_token=ADMIN, environ=ENVIRON), base_url=LOCAL
    )


def _pending_run(workspace: Path, tmp_path: Path) -> tuple[str, str]:
    task_file = tmp_path / "task.yaml"
    task_file.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(workspace, task_file)
    execution = application.start_run(workspace, task.task_id)
    assert execution.current_phase.value == "DECISION"
    assert execution.change_set_digest
    return execution.execution_id, execution.change_set_digest


def _decision(digest: str, **extra: object) -> dict[str, object]:
    return {
        "decision": "APPROVE",
        "change_set_digest": digest,
        "rationale": "API evidence reviewed",
        **extra,
    }


def _audit(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / ".harness" / AUDIT_LOG
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _files_containing(root: Path, needle: bytes) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file() and needle in path.read_bytes()]


@pytest.mark.parametrize("path", ["/", "/api/runs", "/api/health", "/docs", "/openapi.json"])
def test_every_route_requires_a_token(secured: TestClient, path: str) -> None:
    response = secured.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    wrong = secured.get(path, headers=_bearer(_token("unknown")))
    assert wrong.status_code == 401
    basic = secured.get(path, headers={"Authorization": f"Basic {ADMIN}"})
    assert basic.status_code == 401


def test_the_dashboard_asks_for_the_token_and_sends_it_from_request(secured: TestClient) -> None:
    sign_in = secured.get("/")
    assert sign_in.status_code == 401
    assert "sessionStorage" in sign_in.text
    assert 'type="password"' in sign_in.text
    page = secured.get("/", headers=_bearer(VIEWER))
    assert page.status_code == 200
    assert "Governed Agent Harness" in page.text
    assert "headers.set('Authorization','Bearer '+token)" in page.text
    # The token is only accepted in the header, never in the query string.
    assert secured.get(f"/api/runs?token={VIEWER}").status_code == 401


def test_viewer_reads_but_cannot_decide_or_read_the_configuration(
    secured: TestClient, python_workspace: Path, tmp_path: Path
) -> None:
    run, digest = _pending_run(python_workspace, tmp_path)
    assert secured.get("/api/runs", headers=_bearer(VIEWER)).status_code == 200
    assert secured.get(f"/api/runs/{run}/review", headers=_bearer(VIEWER)).status_code == 200
    session = secured.get("/api/session", headers=_bearer(VIEWER)).json()
    assert session == {"authentication": "token", "userId": "bob", "role": "viewer"}
    assert secured.get("/api/config", headers=_bearer(VIEWER)).status_code == 403
    assert secured.get("/api/config", headers=_bearer(REVIEWER)).status_code == 403
    refused = secured.post(
        f"/api/runs/{run}/decision", json=_decision(digest), headers=_bearer(VIEWER)
    )
    assert refused.status_code == 403
    assert "reviewer" in refused.json()["detail"]
    status = HarnessApplication().status(python_workspace, run)
    assert status["humanDecision"] is None
    outcomes = [
        (item["userId"], item["outcome"], item.get("status")) for item in _audit(python_workspace)
    ]
    assert outcomes == [("bob", "attempted", None), ("bob", "refused", 403)]


def test_admin_reads_the_effective_configuration(secured: TestClient) -> None:
    response = secured.get("/api/config", headers=_bearer(ADMIN))
    assert response.status_code == 200
    api = response.json()["api"]
    assert api["auth"] == "token"
    assert api["tokenUser"] == "human.owner"
    assert [user["id"] for user in api["users"]] == ["alice", "bob"]
    assert ADMIN not in response.text
    assert REVIEWER not in response.text


def test_the_authenticated_reviewer_is_the_decider_and_the_decision_is_audited(
    secured: TestClient, python_workspace: Path, tmp_path: Path
) -> None:
    run, digest = _pending_run(python_workspace, tmp_path)
    mismatch = secured.post(
        f"/api/runs/{run}/decision",
        json=_decision(digest, actor_id="mallory"),
        headers=_bearer(REVIEWER),
    )
    assert mismatch.status_code == 403
    assert "alice" in mismatch.json()["detail"]
    agent = secured.post(
        f"/api/runs/{run}/decision",
        json=_decision(digest, actor_id="agent.claude-code"),
        headers=_bearer(REVIEWER),
    )
    assert agent.status_code == 403
    assert HarnessApplication().status(python_workspace, run)["humanDecision"] is None

    recorded = secured.post(
        f"/api/runs/{run}/decision",
        json=_decision(digest, actor_id="alice"),
        headers=_bearer(REVIEWER),
    )
    assert recorded.status_code == 200, recorded.text
    decision = recorded.json()["decision"]
    assert decision["actor"]["actorId"] == "alice"
    assert recorded.json()["execution"]["status"] == "PASSED"

    records = _audit(python_workspace)
    assert [(item["outcome"], item.get("status")) for item in records] == [
        ("attempted", None),
        ("refused", 403),
        ("attempted", None),
        ("refused", 403),
        ("attempted", None),
        ("recorded", 200),
    ]
    last = records[-1]
    assert last["userId"] == "alice"
    assert last["role"] == "reviewer"
    assert last["route"] == f"POST /api/runs/{run}/decision"
    assert last["executionId"] == run
    assert last["decision"] == "APPROVE"
    assert last["changeSetDigest"] == digest
    assert last["decisionId"] == decision["decisionId"]
    assert last["clientHost"]
    assert last["time"]
    # No token anywhere under .harness/: audit log, events, artifacts, state.
    for token in (ADMIN, REVIEWER, VIEWER):
        assert _files_containing(python_workspace / ".harness", token.encode()) == []


def test_a_decision_without_actor_id_records_the_start_token_user(
    secured: TestClient, python_workspace: Path, tmp_path: Path
) -> None:
    run, digest = _pending_run(python_workspace, tmp_path)
    recorded = secured.post(
        f"/api/runs/{run}/decision", json=_decision(digest, actor_id=""), headers=_bearer(ADMIN)
    )
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["decision"]["actor"]["actorId"] == "human.owner"


def test_untrusted_hosts_are_refused_before_authentication(python_workspace: Path) -> None:
    _write_api(python_workspace, SECTION)
    rebound = TestClient(
        create_app(python_workspace, start_token=ADMIN, environ=ENVIRON),
        base_url="http://attacker.example",
    )
    assert rebound.get("/api/runs", headers=_bearer(ADMIN)).status_code == 400


def test_without_the_section_the_api_has_no_authentication(python_workspace: Path) -> None:
    client = TestClient(create_app(python_workspace), base_url=LOCAL)
    assert client.get("/api/runs").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/session").json() == {"authentication": "off"}
    assert client.get("/api/config").json()["api"] == {"auth": "off"}
    assert not (python_workspace / ".harness" / AUDIT_LOG).exists()


def test_auth_off_keeps_the_api_open(python_workspace: Path) -> None:
    config = python_workspace / ".harness" / "project.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "api:\n  auth: off\n", encoding="utf-8")
    client = TestClient(create_app(python_workspace), base_url=LOCAL)
    assert client.get("/api/runs").status_code == 200


def test_the_start_token_comes_from_its_variable_when_not_given(python_workspace: Path) -> None:
    _write_api(python_workspace, SECTION)
    client = TestClient(
        create_app(python_workspace, environ={"TEST_API_START": ADMIN}), base_url=LOCAL
    )
    assert client.get("/api/session", headers=_bearer(ADMIN)).json()["userId"] == "human.owner"
    app = create_app(python_workspace, environ={})
    assert any("every request will be refused" in item for item in app.state.auth_notices)
    assert any("alice cannot sign in" in item for item in app.state.auth_notices)


def test_invalid_sections_and_short_tokens_stop_the_server(python_workspace: Path) -> None:
    _write_api(
        python_workspace,
        {**SECTION, "users": [{"id": "agent.x", "role": "admin", "tokenEnv": "X"}]},
    )
    with pytest.raises(ConfigurationError, match="agent"):
        create_app(python_workspace)
    _write_api(python_workspace, SECTION)
    with pytest.raises(ConfigurationError, match="TEST_API_REVIEWER") as caught:
        create_app(python_workspace, start_token=ADMIN, environ={"TEST_API_REVIEWER": "short"})
    assert "short" not in str(caught.value)
    with pytest.raises(ConfigurationError, match="same token"):
        create_app(python_workspace, start_token=ADMIN, environ={"TEST_API_REVIEWER": ADMIN})


def _serve(workspace: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Any:
    import uvicorn

    served: list[object] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **_: served.append(app))
    for name in ("TEST_API_START", "TEST_API_REVIEWER", "TEST_API_VIEWER"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(cli.app, ["api", "serve", "--path", str(workspace)])
    return result, served


def test_api_serve_shows_a_generated_token_once(
    python_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_api(python_workspace, SECTION)
    result, served = _serve(python_workspace, monkeypatch, {})
    assert result.exit_code == 0, result.stderr
    assert len(served) == 1
    line = next(item for item in result.stderr.splitlines() if "shown once" in item)
    token = line.rsplit(" ", 1)[-1]
    assert len(token) >= 32
    assert result.stderr.count(token) == 1
    assert token not in result.stdout
    client = TestClient(served[0], base_url=LOCAL)  # type: ignore[arg-type]
    assert client.get("/api/session", headers=_bearer(token)).json()["role"] == "admin"
    assert _files_containing(python_workspace / ".harness", token.encode()) == []


def test_api_serve_does_not_print_a_token_from_the_environment(
    python_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_api(python_workspace, SECTION)
    result, served = _serve(python_workspace, monkeypatch, {"TEST_API_START": ADMIN})
    assert result.exit_code == 0, result.stderr
    assert ADMIN not in result.stderr + result.stdout
    assert "taken from TEST_API_START" in result.stderr
    assert "bob cannot sign in" in result.stderr


def test_api_serve_without_the_section_warns_that_there_is_no_authentication(
    python_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, served = _serve(python_workspace, monkeypatch, {})
    assert result.exit_code == 0, result.stderr
    assert "no authentication" in result.stderr
    assert len(served) == 1


def test_api_serve_refuses_an_invalid_section(
    python_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_api(python_workspace, {"auth": "token", "token": "inline"})
    result, served = _serve(python_workspace, monkeypatch, {})
    assert result.exit_code != 0
    assert served == []
    assert "invalid api section" in result.stderr
    assert "inline" not in result.stderr  # the offending value is not echoed
