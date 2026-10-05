"""Configuration keys of the reviewer's experience (#53): absent keys keep 1.0.0 behaviour and
the configuration snapshot; present keys validate strictly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.errors import ConfigurationError

SECTIONS = ("review", "notifications", "retrospective")


def _edit(workspace: Path, **sections: Any) -> None:
    path = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    for key, section in sections.items():
        if section is None:
            value.pop(key, None)
        else:
            value[key] = section
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def test_absent_sections_keep_the_snapshot_and_the_defaults(python_workspace: Path) -> None:
    _edit(python_workspace, review=None, notifications=None, retrospective=None)
    _edit(python_workspace, verification={"requirementTraceability": "enforce"})
    resolved = ConfigurationResolver().resolve(python_workspace)
    project = resolved.project
    assert not project.exceptions_enabled
    assert not project.output_parsers_enabled
    assert not project.causal_retrospective
    assert project.webhooks == ()
    dumped = resolved.model_dump(mode="json", by_alias=True)["project"]
    for section in SECTIONS:
        assert section not in dumped
    assert dumped["verification"] == {"requirementTraceability": "enforce"}


def test_present_keys_are_resolved(python_workspace: Path) -> None:
    _edit(
        python_workspace,
        review={"exceptions": True, "exceptionDays": 14},
        retrospective={"causal": True},
        verification={"requirementTraceability": "warn", "outputParsers": True},
        notifications={"webhooks": [{"urlEnv": "HARNESS_HOOK", "events": ["run.finished"]}]},
    )
    project = ConfigurationResolver().resolve(python_workspace).project
    assert project.exceptions_enabled and project.exception_days == 14
    assert project.output_parsers_enabled
    assert project.causal_retrospective
    assert project.webhooks[0].url_env == "HARNESS_HOOK"
    review = HarnessApplication().validate_config(python_workspace)["review"]
    assert review["webhooks"] == [
        {"target": "env:HARNESS_HOOK", "events": ["run.finished"], "retries": 2}
    ]


@pytest.mark.parametrize(
    "webhook",
    [
        {},
        {"url": "http://localhost/x", "urlEnv": "X"},
        {"url": "file:///etc/passwd"},
        {"urlEnv": "NOT A NAME"},
        {"url": "https://example.invalid/x", "events": []},
        {"url": "https://example.invalid/x", "events": ["run.started"]},
    ],
)
def test_invalid_webhooks_are_configuration_errors(
    python_workspace: Path, webhook: dict[str, Any]
) -> None:
    _edit(python_workspace, notifications={"webhooks": [webhook]})
    with pytest.raises(ConfigurationError):
        ConfigurationResolver().resolve(python_workspace)


def test_exception_days_are_bounded(python_workspace: Path) -> None:
    _edit(python_workspace, review={"exceptions": True, "exceptionDays": 0})
    with pytest.raises(ConfigurationError):
        ConfigurationResolver().resolve(python_workspace)
