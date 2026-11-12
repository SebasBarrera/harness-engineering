"""``harness doctor`` on Windows without long path support: a state directory whose artifact
files would exceed the path limit is reported before ``run start`` fails on it."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from governed_harness.application import service
from governed_harness.evidence import artifact_store


def _project() -> SimpleNamespace:
    return SimpleNamespace(project_id="sample", runtime=SimpleNamespace(state_dir=None))


def test_a_state_directory_too_long_for_windows_fails_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(artifact_store.sys, "platform", "win32")
    monkeypatch.setattr(artifact_store, "windows_long_paths_enabled", lambda: False)
    long_workspace = tmp_path / ("w" * max(1, 190 - len(str(tmp_path))))
    long_workspace.mkdir()
    check = service._path_length_check(_project(), long_workspace)
    assert check["status"] == "FAILED"
    assert check["path"] == str(long_workspace.resolve() / ".harness" / "artifacts")
    assert "runtime.stateDir" in check["hint"]
    assert "260" in check["message"]

    short = tmp_path / "s"
    short.mkdir()
    assert service._path_length_check(_project(), short)["status"] == "PASSED"


def test_long_paths_enabled_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifact_store.sys, "platform", "win32")
    monkeypatch.setattr(artifact_store, "windows_long_paths_enabled", lambda: True)
    long_workspace = tmp_path / ("w" * max(1, 190 - len(str(tmp_path))))
    long_workspace.mkdir()
    assert service._path_length_check(_project(), long_workspace)["status"] == "PASSED"
