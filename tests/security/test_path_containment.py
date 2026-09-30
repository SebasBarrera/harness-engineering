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
