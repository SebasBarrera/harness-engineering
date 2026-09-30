from pathlib import Path

import pytest

from governed_harness.capabilities.authorizer import CapabilityDenied, contained_path


def test_path_inside_workspace(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    assert contained_path(tmp_path, child) == child.resolve()


def test_path_traversal_is_denied(tmp_path: Path) -> None:
    with pytest.raises(CapabilityDenied):
        contained_path(tmp_path, tmp_path / ".." / "outside")


def test_absolute_path_under_a_symlinked_ancestor_is_contained(tmp_path: Path) -> None:
    """A workspace reached through a symlinked ancestor (macOS /var -> /private/var) (#19)."""
    real = tmp_path / "real"
    (real / "workspace").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    workspace = alias / "workspace"
    inside = workspace / "src" / "module.py"
    assert contained_path(workspace, inside) == (real / "workspace" / "src" / "module.py").resolve()
    assert contained_path(real / "workspace", inside).name == "module.py"


def test_symlink_inside_the_workspace_is_still_rejected_through_an_alias(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "workspace" / "target").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    (real / "workspace" / "link").symlink_to(
        real / "workspace" / "target", target_is_directory=True
    )
    with pytest.raises(CapabilityDenied):
        contained_path(alias / "workspace", alias / "workspace" / "link" / "file.txt")
    with pytest.raises(CapabilityDenied):
        contained_path(alias / "workspace", tmp_path / "outside.txt")
