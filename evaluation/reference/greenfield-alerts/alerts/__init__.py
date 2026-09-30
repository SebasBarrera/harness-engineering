"""Reference implementation used only to validate the hidden tests."""

import base64
import json
import os
import urllib.request

_SEVERITIES = {"info", "warning", "critical"}


def build_alert_request(event, *, base_url="https://alerts.internal.example"):
    severity, message = event.get("severity"), event.get("message")
    if severity not in _SEVERITIES or not isinstance(message, str) or not message.strip():
        raise ValueError("invalid event")
    secret = os.environ.get("ALERTS_PASSWORD")
    if not secret:
        raise RuntimeError("ALERTS_PASSWORD is not set")
    body = json.dumps(
        {"message": message.strip(), "severity": severity, "source": event.get("source", "app")},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    token = base64.b64encode(f"alerts:{secret}".encode()).decode("ascii")
    return urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/alerts", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
    )
