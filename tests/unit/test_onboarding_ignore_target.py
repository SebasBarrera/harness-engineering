"""The ignore file init writes stays inside its workspace or Git directory (#86)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_harness.application import onboarding


def test_the_gitignore_inside_the_workspace_is_written(tmp_path: Path) -> None:
    status = onboarding.ensure_gitignore(tmp_path)
    assert status == "created"
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == ".harness/\n"


def test_an_ignore_file_outside_its_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "elsewhere" / ".gitignore"
    with pytest.raises(ValueError, match="outside"):
        onboarding._add_entry(outside, root)
    assert not outside.exists()


def test_a_file_that_is_not_an_ignore_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "settings.py"
    with pytest.raises(ValueError, match="outside"):
        onboarding._add_entry(target, tmp_path)
    assert not target.exists()
