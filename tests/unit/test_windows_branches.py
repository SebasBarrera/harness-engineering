"""The Windows-only branches, exercised on every platform by setting ``sys.platform`` in the
module that reads it: CI measures coverage on Linux, and these branches decide real behaviour on
Windows (absolute state directories, ``file:///C:/`` URIs, traceback frames, PATHEXT lookup)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from governed_harness.configuration import models
from governed_harness.runtime import process_runner
from governed_harness.validators import parsers


@pytest.mark.parametrize("value", ["C:\\Users\\me\\state", "D:/state", "\\\\server\\share\\state"])
def test_windows_absolute_state_dirs_are_accepted_on_windows(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setattr(models.sys, "platform", "win32")
    assert models._is_state_dir_path(value)


@pytest.mark.parametrize("value", ["C:\\Users\\me\\state", "state", "C:state"])
def test_windows_paths_are_not_absolute_state_dirs_elsewhere(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setattr(models.sys, "platform", "linux")
    assert not models._is_state_dir_path(value)


def test_posix_state_dirs_are_accepted_on_every_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    for platform in ("linux", "darwin", "win32"):
        monkeypatch.setattr(models.sys, "platform", platform)
        assert models._is_state_dir_path("/var/state")
        assert models._is_state_dir_path("~/state")


def test_a_drive_uri_loses_its_leading_slash_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parsers.sys, "platform", "win32")
    # Outside the (POSIX) workspace, so the path is kept as written after the scheme.
    assert parsers._relative("file:///C:/elsewhere/x.py", Path("/ws")) == "C:/elsewhere/x.py"


def test_a_drive_uri_is_left_alone_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parsers.sys, "platform", "linux")
    assert parsers._relative("file:///C:/elsewhere/x.py", Path("/ws")) == "/C:/elsewhere/x.py"


def test_traceback_frames_match_either_separator_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parsers.sys, "platform", "win32")
    pattern = parsers._frame_path("tests/test_x.py")
    assert re.match(rf"^{pattern}:(\d+): ", "tests\\test_x.py:12: AssertionError")
    assert re.match(rf"^{pattern}:(\d+): ", "tests/test_x.py:12: AssertionError")


def test_traceback_frames_match_only_the_node_id_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parsers.sys, "platform", "linux")
    pattern = parsers._frame_path("tests/test_x.py")
    assert re.match(rf"^{pattern}:(\d+): ", "tests/test_x.py:12: AssertionError")
    assert not re.match(rf"^{pattern}:(\d+): ", "tests\\test_x.py:12: AssertionError")


def test_bare_names_resolve_through_path_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_runner.sys, "platform", "win32")
    found = {"npm": "C:\\nodejs\\npm.cmd"}
    monkeypatch.setattr(process_runner.shutil, "which", found.get)
    assert process_runner.resolve_executable("npm") == "C:\\nodejs\\npm.cmd"
    assert process_runner.resolve_executable("missing") == "missing"


def test_bare_names_are_unchanged_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_runner.sys, "platform", "linux")
    assert process_runner.resolve_executable("npm") == "npm"
