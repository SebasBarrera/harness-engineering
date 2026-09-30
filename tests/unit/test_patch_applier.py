from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from governed_harness.capabilities import CapabilityDenied, grants_from_rules
from governed_harness.configuration.models import CapabilityRule
from governed_harness.domain.enums import ActorType
from governed_harness.domain.models import Actor, FilePatch
from governed_harness.runtime import PatchApplier


def context(tmp_path: Path, *, scope: tuple[str, ...] = ("src/**",), conditions=None):
    actor = Actor(actor_type=ActorType.AGENT, actor_id="agent.patch")
    rule = CapabilityRule(capability="filesystem.write", scope=scope, conditions=conditions or {})
    grants = grants_from_rules("run_1", actor, (rule,), lifetime=timedelta(minutes=1))
    return actor, grants, PatchApplier(tmp_path)


def test_create_replace_and_append(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    actor, grants, applier = context(tmp_path)
    path = applier.apply(FilePatch(path="src/a.py", operation="create", content="a=1\n"), actor=actor, grants=grants)
    applier.apply(FilePatch(path="src/a.py", operation="replace", content="a=2\n"), actor=actor, grants=grants)
    applier.apply(FilePatch(path="src/a.py", operation="append", content="b=3\n"), actor=actor, grants=grants)
    assert path.read_text() == "a=2\nb=3\n"


def test_idempotent_create_with_identical_content(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("same")
    actor, grants, applier = context(tmp_path)
    applier.apply(FilePatch(path="src/a.py", operation="create", content="same"), actor=actor, grants=grants)


def test_delete_is_denied_without_explicit_condition(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x")
    actor, grants, applier = context(tmp_path)
    with pytest.raises(CapabilityDenied):
        applier.apply(FilePatch(path="src/a.py", operation="delete"), actor=actor, grants=grants)


def test_symlink_write_is_denied(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "src").symlink_to(outside, target_is_directory=True)
    actor, grants, applier = context(root)
    with pytest.raises(CapabilityDenied):
        applier.apply(FilePatch(path="src/a.py", operation="create", content="x"), actor=actor, grants=grants)
