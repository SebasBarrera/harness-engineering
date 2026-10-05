from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver, RuntimeConfig
from governed_harness.configuration.models import DEFAULT_SANDBOX_WRITE_PATHS
from governed_harness.domain.errors import ConfigurationError


def _set_runtime(workspace: Path, **values: object) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text())
    for key, value in values.items():
        if value is None:
            config["runtime"].pop(key, None)
        else:
            config["runtime"][key] = value
    path.write_text(yaml.safe_dump(config, sort_keys=False))


def test_init_enforces_the_sandbox_with_the_default_write_paths(python_workspace: Path) -> None:
    runtime = yaml.safe_load((python_workspace / ".harness" / "project.yaml").read_text())[
        "runtime"
    ]
    assert runtime["agentSandbox"] == "enforce"
    assert runtime["sandboxWritePaths"] == [path for path, _ in DEFAULT_SANDBOX_WRITE_PATHS]
    resolved = ConfigurationResolver().resolve(python_workspace)
    assert resolved.project.runtime.effective_agent_sandbox == "enforce"
    assert HarnessApplication().validate_config(python_workspace)["agentSandbox"] == {
        "mode": "enforce",
        "writePaths": runtime["sandboxWritePaths"],
    }


def test_project_file_without_the_sandbox_keys_is_off_and_keeps_its_snapshot(
    python_workspace: Path,
) -> None:
    """A project.yaml written by 1.0.0 has neither key: the agent runs unconfined, as before,
    and the resolved configuration serializes without them, so its digest does not change."""
    _set_runtime(python_workspace, agentSandbox=None, sandboxWritePaths=None)
    resolved = ConfigurationResolver().resolve(python_workspace)
    assert resolved.project.runtime.agent_sandbox is None
    assert resolved.project.runtime.effective_agent_sandbox == "off"
    runtime = resolved.model_dump(mode="json", by_alias=True)["project"]["runtime"]
    assert set(runtime) == {
        "commandTimeoutSeconds",
        "maxOutputBytes",
        "maxParallel",
        "allowNetwork",
    }


def test_a_bare_yaml_off_means_off(python_workspace: Path) -> None:
    path = python_workspace / ".harness" / "project.yaml"
    path.write_text(path.read_text().replace("agentSandbox: enforce", "agentSandbox: off"))
    assert "agentSandbox: off\n" in path.read_text()
    runtime = ConfigurationResolver().resolve(python_workspace).project.runtime
    assert runtime.effective_agent_sandbox == "off"


@pytest.mark.parametrize(
    "path",
    ["relative/dir", "$HOME/.cache", "~other/.cache", "/a/*/b", "/a/**", "/", '/a"b', "/a\nb"],
)
def test_invalid_sandbox_write_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        RuntimeConfig.model_validate({"agentSandbox": "enforce", "sandboxWritePaths": [path]})


def test_unknown_sandbox_mode_is_a_configuration_error(python_workspace: Path) -> None:
    _set_runtime(python_workspace, agentSandbox="strict")
    with pytest.raises(ConfigurationError):
        ConfigurationResolver().resolve(python_workspace)
