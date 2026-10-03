from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import AcceptanceCriterion, Requirement
from governed_harness.intake import ClarificationInput

from .task_loader import _first, _required_text

_KNOWN_FIELDS = frozenset({"answers", "replaceCriteria", "addCriteria", "addRequirements"})


def _criterion(item: Any, *, require_id: bool, where: str) -> AcceptanceCriterion:
    if isinstance(item, str) and not require_id:
        if not item.strip():
            raise ConfigurationError(f"{where} requires a non-empty text")
        return AcceptanceCriterion(criterion_id=new_id("ac"), text=item)
    if not isinstance(item, dict):
        raise ConfigurationError(f"{where} must be an object with criterionId and text")
    criterion_id = _first(item, "criterionId", "criterion_id", "id")
    if criterion_id is None and require_id:
        raise ConfigurationError(f"{where} requires the criterionId it replaces")
    try:
        return AcceptanceCriterion(
            criterion_id=str(criterion_id) if criterion_id is not None else new_id("ac"),
            text=_required_text(item, "text", where),
            verification_hint=_first(item, "verificationHint", "verification_hint"),
            priority=cast(
                Literal["MUST", "SHOULD", "COULD"],
                str(_first(item, "priority", default="MUST")).upper(),
            ),
        )
    except ConfigurationError:
        raise
    except Exception as error:
        raise ConfigurationError(f"invalid {where}: {error}") from error


def _requirement(item: Any) -> Requirement:
    if isinstance(item, str):
        if not item.strip():
            raise ConfigurationError("an added requirement requires a non-empty text")
        return Requirement(requirement_id=new_id("req"), text=item, source="clarification")
    if not isinstance(item, dict):
        raise ConfigurationError("added requirements must be strings or objects")
    return Requirement(
        requirement_id=str(
            _first(item, "requirementId", "requirement_id", "id", default=new_id("req"))
        ),
        text=_required_text(item, "text", "an added requirement"),
        source="clarification",
    )


def _list(raw: dict[str, Any], name: str) -> list[Any]:
    value = raw.get(name) or []
    if not isinstance(value, list):
        raise ConfigurationError(f"{name} must be a list")
    return value


def load_clarification_file(path: Path) -> ClarificationInput:
    """Read an answers file (YAML or JSON).

    ``answers`` maps question ids to answer text. Optional task changes:
    ``replaceCriteria`` (objects with the ``criterionId`` they replace), ``addCriteria`` and
    ``addRequirements`` (strings or objects; added requirements get source
    ``clarification``)."""
    try:
        if path.suffix.lower() == ".json":
            raw = json.loads(path.read_text(encoding="utf-8"))
        else:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ConfigurationError(f"cannot parse answers file {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigurationError("answers file must contain an object")
    unknown = sorted(set(raw) - _KNOWN_FIELDS)
    if unknown:
        raise ConfigurationError(f"unknown answers-file field(s): {', '.join(unknown)}")
    answers = raw.get("answers")
    if not isinstance(answers, dict) or not answers:
        raise ConfigurationError("answers file requires 'answers': a map of question id to text")
    texts: dict[str, str] = {}
    for question_id, text in answers.items():
        if text is None or not isinstance(text, str | int | float):
            raise ConfigurationError(f"empty answer(s): {question_id}")
        texts[str(question_id)] = str(text)
    return ClarificationInput(
        answers=texts,
        replace_criteria=tuple(
            _criterion(item, require_id=True, where="a replaced criterion")
            for item in _list(raw, "replaceCriteria")
        ),
        add_criteria=tuple(
            _criterion(item, require_id=False, where="an added criterion")
            for item in _list(raw, "addCriteria")
        ),
        add_requirements=tuple(_requirement(item) for item in _list(raw, "addRequirements")),
    )
