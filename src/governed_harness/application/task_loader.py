from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    AcceptanceCriterion,
    FilePatch,
    ImplementationInstruction,
    Requirement,
    Task,
)


def _first(value: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return default


def load_task_file(path: Path, *, project_id: str) -> Task:
    try:
        if path.suffix.lower() == ".json":
            raw = json.loads(path.read_text(encoding="utf-8"))
        else:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ConfigurationError(f"cannot parse task file {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigurationError("task file must contain an object")
    requirements = []
    for item in _first(raw, "requirements", default=[]) or []:
        if isinstance(item, str):
            requirements.append(
                Requirement(requirement_id=new_id("req"), text=item, source="human")
            )
        elif isinstance(item, dict):
            requirements.append(
                Requirement(
                    requirement_id=_first(item, "requirementId", "requirement_id", "id", default=new_id("req")),
                    text=str(_first(item, "text")),
                    source=str(_first(item, "source", default="human")),
                )
            )
        else:
            raise ConfigurationError("requirements must be strings or objects")
    criteria = []
    for item in _first(raw, "acceptanceCriteria", "acceptance_criteria", default=[]) or []:
        if isinstance(item, str):
            criteria.append(
                AcceptanceCriterion(criterion_id=new_id("ac"), text=item)
            )
        elif isinstance(item, dict):
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=_first(item, "criterionId", "criterion_id", "id", default=new_id("ac")),
                    text=str(_first(item, "text")),
                    verification_hint=_first(item, "verificationHint", "verification_hint"),
                    priority=cast(
                        Literal["MUST", "SHOULD", "COULD"],
                        str(_first(item, "priority", default="MUST")).upper(),
                    ),
                )
            )
        else:
            raise ConfigurationError("acceptance criteria must be strings or objects")
    implementation_raw = _first(raw, "implementation", default={}) or {}
    patches = []
    for patch in _first(implementation_raw, "patches", default=[]) or []:
        patches.append(
            FilePatch(
                path=str(_first(patch, "path")),
                operation=cast(
                    Literal["create", "replace", "append", "delete"], str(_first(patch, "operation"))
                ),
                content=_first(patch, "content"),
                expected_sha256=_first(patch, "expectedSha256", "expected_sha256"),
            )
        )
    implementation = ImplementationInstruction(
        mode=cast(
            Literal["none", "patch", "command"], str(_first(implementation_raw, "mode", default="none"))
        ),
        patches=tuple(patches),
        argv=tuple(str(item) for item in (_first(implementation_raw, "argv", default=[]) or [])),
        cwd=str(_first(implementation_raw, "cwd", default=".")),
    )
    try:
        return Task(
            task_id=str(_first(raw, "taskId", "task_id", default=new_id("task"))),
            project_id=str(_first(raw, "projectId", "project_id", default=project_id)),
            title=str(_first(raw, "title")),
            intent=str(_first(raw, "intent")),
            constraints=tuple(str(item) for item in (_first(raw, "constraints", default=[]) or [])),
            requirements=tuple(requirements),
            acceptance_criteria=tuple(criteria),
            implementation=implementation,
            metadata=dict(_first(raw, "metadata", default={}) or {}),
        )
    except Exception as error:
        raise ConfigurationError(f"invalid task definition: {error}") from error
