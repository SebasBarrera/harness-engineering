from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.errors import ConfigurationError


def test_python_profile_is_auto_detected(python_workspace: Path) -> None:
    resolved = ConfigurationResolver().resolve(python_workspace)
    assert [item.profile_id for item in resolved.profiles] == ["python_default"]
    assert "python.pytest" in [item.validator_id for item in resolved.effective_validators]


def test_node_profile_is_auto_detected(node_workspace: Path) -> None:
    resolved = ConfigurationResolver().resolve(node_workspace)
    assert [item.profile_id for item in resolved.profiles] == ["node_default"]


def test_locked_policy_cannot_be_disabled(python_workspace: Path) -> None:
    path = python_workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    value["policies"]["requireHumanDecision"] = False
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    with pytest.raises(ConfigurationError):
        ConfigurationResolver().resolve(python_workspace)


def test_config_validate_reports_effective_controls(python_workspace: Path) -> None:
    result = HarnessApplication().validate_config(python_workspace)
    assert result["status"] == "PASSED"
    assert result["policies"]["approvalDigestBinding"] is True
    assert any(item["capability"] == "filesystem.write" for item in result["capabilities"])
