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


def test_retrospective_auto_apply_cannot_be_enabled(python_workspace: Path) -> None:
    """RD-11: no project configuration lets the retrospective apply its own recommendations."""
    path = python_workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    value["policies"]["retrospectiveAutoApply"] = True
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    with pytest.raises(ConfigurationError, match="retrospectiveAutoApply"):
        ConfigurationResolver().resolve(python_workspace)


def test_config_validate_reports_effective_controls(python_workspace: Path) -> None:
    result = HarnessApplication().validate_config(python_workspace)
    assert result["status"] == "PASSED"
    assert result["policies"]["approvalDigestBinding"] is True
    assert any(item["capability"] == "filesystem.write" for item in result["capabilities"])


def _set_intake(workspace: Path, intake: dict[str, str] | None) -> None:
    path = workspace / ".harness" / "project.yaml"
    value = yaml.safe_load(path.read_text())
    if intake is None:
        del value["intake"]
    else:
        value["intake"] = intake
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def test_init_writes_the_enforce_criteria_policy(python_workspace: Path) -> None:
    value = yaml.safe_load((python_workspace / ".harness" / "project.yaml").read_text())
    assert value["intake"] == {"criteriaPolicy": "enforce"}
    resolved = ConfigurationResolver().resolve(python_workspace)
    assert resolved.project.criteria_policy == "enforce"
    assert HarnessApplication().validate_config(python_workspace)["intake"] == {
        "criteriaPolicy": "enforce"
    }


def test_project_file_without_intake_uses_warn_and_keeps_its_snapshot(
    python_workspace: Path,
) -> None:
    """A project.yaml written by 1.0.0 has no intake section: it behaves as warn and its
    resolved configuration serializes without the section, so the configuration snapshot of an
    existing project keeps its content and digest."""
    _set_intake(python_workspace, None)
    resolved = ConfigurationResolver().resolve(python_workspace)
    assert resolved.project.intake is None
    assert resolved.project.criteria_policy == "warn"
    assert "intake" not in resolved.model_dump(mode="json", by_alias=True)["project"]


@pytest.mark.parametrize("policy", ["enforce", "warn", "off"])
def test_criteria_policy_values(python_workspace: Path, policy: str) -> None:
    _set_intake(python_workspace, {"criteriaPolicy": policy})
    assert ConfigurationResolver().resolve(python_workspace).project.criteria_policy == policy


def test_unknown_criteria_policy_is_a_configuration_error(python_workspace: Path) -> None:
    _set_intake(python_workspace, {"criteriaPolicy": "strict"})
    with pytest.raises(ConfigurationError):
        ConfigurationResolver().resolve(python_workspace)
