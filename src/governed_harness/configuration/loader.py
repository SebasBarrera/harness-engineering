from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from governed_harness.configuration.models import (
    ProjectConfiguration,
    TechnologyProfileDefinition,
    WorkflowDefinition,
)
from governed_harness.domain.errors import ConfigurationError


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigurationError(f"cannot read configuration: {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigurationError(f"invalid YAML in {path}: {error}") from error
    if not isinstance(value, dict):
        raise ConfigurationError(f"configuration must be an object: {path}")
    return value


def find_project_config(start: Path) -> Path:
    current = start.resolve(strict=True)
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        config = candidate / ".harness" / "project.yaml"
        if config.exists():
            return config
    raise ConfigurationError(f"no .harness/project.yaml found from {start}")


def load_project_config(path: Path) -> ProjectConfiguration:
    config_path = path if path.name.endswith(".yaml") else find_project_config(path)
    value = load_yaml(config_path)
    try:
        return ProjectConfiguration.model_validate(value)
    except Exception as error:
        raise ConfigurationError(f"invalid project configuration {config_path}: {error}") from error


def resource_text(*parts: str) -> str:
    target = resources.files("governed_harness.resources")
    for part in parts:
        target = target.joinpath(part)
    return target.read_text(encoding="utf-8")


_BUILTIN_PROFILES = {
    "python": "python.yaml",
    "python_default": "python.yaml",
    "node": "node.yaml",
    "node_default": "node.yaml",
}
BUILTIN_PROFILE_IDS = frozenset(_BUILTIN_PROFILES)
"""Ids (and aliases) of the built-in profiles; a project profile cannot reuse them."""


def load_builtin_profile(profile_id: str) -> TechnologyProfileDefinition:
    filename = _BUILTIN_PROFILES.get(profile_id)
    if filename is None:
        raise ConfigurationError(f"unknown built-in profile: {profile_id}")
    try:
        return TechnologyProfileDefinition.model_validate(
            yaml.safe_load(resource_text("profiles", filename))
        )
    except Exception as error:
        raise ConfigurationError(f"invalid built-in profile {profile_id}: {error}") from error


def load_builtin_workflow(workflow_id: str) -> WorkflowDefinition:
    if workflow_id != "default_development":
        raise ConfigurationError(f"unknown built-in workflow: {workflow_id}")
    try:
        return WorkflowDefinition.model_validate(
            yaml.safe_load(resource_text("workflows", "default.yaml"))
        )
    except Exception as error:
        raise ConfigurationError(f"invalid built-in workflow {workflow_id}: {error}") from error


def validate_json_against_schema(instance: dict[str, Any], schema_name: str) -> list[str]:
    schema_root = resources.files("governed_harness.resources").joinpath("schemas", "v1")
    schemas: dict[str, dict[str, Any]] = {}
    for item in schema_root.iterdir():
        if item.name.endswith(".json"):
            parsed = json.loads(item.read_text(encoding="utf-8"))
            schemas[parsed.get("$id", item.name)] = parsed
            schemas[item.name] = parsed
    schema = schemas[schema_name]
    registry = Registry()
    for key, value in schemas.items():
        if key.startswith("http"):
            registry = registry.with_resource(key, Resource.from_contents(value))
    validator = Draft202012Validator(schema, registry=registry)
    return [error.message for error in sorted(validator.iter_errors(instance), key=str)]
