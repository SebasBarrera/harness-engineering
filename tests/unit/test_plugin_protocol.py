from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

from governed_harness.capabilities import grants_from_rules
from governed_harness.configuration.models import CapabilityRule
from governed_harness.domain.enums import ActorType, ResultStatus
from governed_harness.domain.models import Actor
from governed_harness.plugins import ExternalPluginClient, PluginProtocolError, PluginRequest


def test_external_echo_plugin_roundtrip(tmp_path: Path) -> None:
    actor = Actor(actor_type=ActorType.PLUGIN, actor_id="plugin.external")
    grants = grants_from_rules(
        "run_1",
        actor,
        (CapabilityRule(capability="process.execute", scope=(sys.executable,)),),
        lifetime=timedelta(minutes=1),
    )
    client = ExternalPluginClient(tmp_path, (sys.executable, "-m", "governed_harness.plugins.sdk"))
    response = client.invoke(
        PluginRequest(requestId="request_001", operation="health", payload={"hello": "world"}),
        grants=grants,
    ).response
    assert response.status is ResultStatus.PASSED
    assert response.payload == {"hello": "world"}


def test_invalid_plugin_stdout_is_rejected(tmp_path: Path) -> None:
    script = tmp_path / "bad.py"
    script.write_text("print('not json')\n")
    actor = Actor(actor_type=ActorType.PLUGIN, actor_id="plugin.external")
    grants = grants_from_rules(
        "run_1",
        actor,
        (CapabilityRule(capability="process.execute", scope=(sys.executable,)),),
        lifetime=timedelta(minutes=1),
    )
    with pytest.raises(PluginProtocolError):
        ExternalPluginClient(tmp_path, (sys.executable, str(script))).invoke(
            PluginRequest(requestId="request_001", operation="health"), grants=grants
        )
