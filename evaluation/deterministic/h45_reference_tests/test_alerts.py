"""Visible tests of the greenfield-alerts reference solution (H45, PREREGISTRO.md).

The hidden acceptance tests of evaluation/hidden/greenfield-alerts with two declared edits:
this docstring names the requirements of the task (req_spec, req_tests), as the traceability
rule of the gate contract asks, and the import block and one `.encode()` call follow the
project's Ruff defaults (I001, UP012). The test functions and their assertions are the same.
"""

import base64
import json

import pytest

from alerts import build_alert_request


@pytest.fixture(autouse=True)
def password(monkeypatch):
    monkeypatch.setenv("ALERTS_PASSWORD", "s3cret-from-env")


def auth(request):
    return request.get_header("Authorization")


def test_request_method_url_and_headers():
    request = build_alert_request({"severity": "info", "message": "hello"})
    assert request.get_method() == "POST"
    assert request.full_url == "https://alerts.internal.example/v1/alerts"
    assert request.get_header("Content-type") == "application/json"


def test_trailing_slash_in_base_url_is_ignored():
    request = build_alert_request({"severity": "info", "message": "x"}, base_url="https://h.example/")
    assert request.full_url == "https://h.example/v1/alerts"


def test_body_is_sorted_compact_utf8_json():
    request = build_alert_request({"severity": "warning", "message": "  café  ", "source": "api"})
    assert request.data == '{"message":"café","severity":"warning","source":"api"}'.encode()


def test_source_defaults_to_app():
    request = build_alert_request({"severity": "critical", "message": "down"})
    assert json.loads(request.data)["source"] == "app"


def test_basic_auth_uses_the_environment_password():
    request = build_alert_request({"severity": "info", "message": "x"})
    expected = base64.b64encode(b"alerts:s3cret-from-env").decode("ascii")
    assert auth(request) == f"Basic {expected}"


def test_password_is_read_at_call_time(monkeypatch):
    monkeypatch.setenv("ALERTS_PASSWORD", "rotated")
    expected = base64.b64encode(b"alerts:rotated").decode("ascii")
    assert auth(build_alert_request({"severity": "info", "message": "x"})) == f"Basic {expected}"


@pytest.mark.parametrize("value", [None, ""])
def test_missing_password_raises_runtime_error(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ALERTS_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("ALERTS_PASSWORD", value)
    with pytest.raises(RuntimeError):
        build_alert_request({"severity": "info", "message": "x"})


@pytest.mark.parametrize(
    "event",
    [
        {"severity": "debug", "message": "x"},
        {"severity": "INFO", "message": "x"},
        {"severity": "info", "message": "   "},
        {"severity": "info", "message": ""},
        {"message": "x"},
        {"severity": "info"},
    ],
)
def test_invalid_events_are_rejected(event):
    with pytest.raises(ValueError):
        build_alert_request(event)
