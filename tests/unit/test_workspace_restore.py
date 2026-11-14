"""``restore_changes`` is all or nothing: a change with a path whose recorded content cannot be
given back (a redacted snapshot, #178) writes nothing, so the workspace is never left half
restored (a new file deleted while the file that imports it keeps the rejected content)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_harness.orchestration import workspace_ops
from governed_harness.orchestration.workspace_ops import Contents, restore_changes
from governed_harness.runtime.workspace import WorkspaceChange, WorkspaceDiff

BASELINE_VALIDATION = b"def validate(ride):\n    return True\n"


def change(path: str, status: str) -> WorkspaceChange:
    return WorkspaceChange(path, status, None, None, 0, 0)


def rejected_workspace(tmp_path: Path) -> Path:
    """The workspace after a rejected attempt: a new ``wiring.py``, a ``platform.py`` that
    imports it and a modified ``validation.py``."""
    source = tmp_path / "src" / "rides"
    source.mkdir(parents=True)
    (source / "wiring.py").write_text("WIRED = True\n")
    (source / "platform.py").write_text("from rides.wiring import WIRED\n")
    (source / "validation.py").write_text("def validate(ride):\n    return False\n")
    return tmp_path


DIFF = WorkspaceDiff(
    changes=(
        change("src/rides/platform.py", "modified"),
        change("src/rides/validation.py", "modified"),
        change("src/rides/wiring.py", "added"),
    ),
    unified_diff=b"",
    digest="sha256:test",
)


def recorded(redacted: set[str]) -> Contents:
    """The baseline: ``platform.py`` and ``validation.py`` existed, ``wiring.py`` did not; the
    content of the ``redacted`` paths cannot be given back."""
    baseline = {
        "src/rides/platform.py": b"PLATFORM = 1\n",
        "src/rides/validation.py": BASELINE_VALIDATION,
    }
    return Contents(
        existed=lambda path: path in baseline,
        content=lambda path: None if path in redacted else baseline.get(path),
    )


def contents(workspace: Path) -> dict[str, str]:
    return {
        path.relative_to(workspace).as_posix(): path.read_text()
        for path in sorted(workspace.rglob("*.py"))
    }


def test_an_unrestorable_path_leaves_the_whole_change_in_place(tmp_path: Path) -> None:
    workspace = rejected_workspace(tmp_path)
    before = contents(workspace)
    restored, unrestorable = restore_changes(
        workspace, recorded({"src/rides/platform.py", "src/rides/validation.py"}), DIFF
    )
    assert restored == []
    assert unrestorable == ["src/rides/platform.py", "src/rides/validation.py"]
    # The new file is not deleted: platform.py still imports it, the tree stays coherent.
    assert contents(workspace) == before


def test_one_unrestorable_path_is_enough_to_write_nothing(tmp_path: Path) -> None:
    workspace = rejected_workspace(tmp_path)
    before = contents(workspace)
    restored, unrestorable = restore_changes(workspace, recorded({"src/rides/platform.py"}), DIFF)
    assert (restored, unrestorable) == ([], ["src/rides/platform.py"])
    assert contents(workspace) == before


def test_every_path_restorable_restores_them_all(tmp_path: Path) -> None:
    workspace = rejected_workspace(tmp_path)
    restored, unrestorable = restore_changes(workspace, recorded(set()), DIFF)
    assert unrestorable == []
    assert restored == [item.path for item in DIFF.changes]
    assert contents(workspace) == {
        "src/rides/platform.py": "PLATFORM = 1\n",
        "src/rides/validation.py": BASELINE_VALIDATION.decode(),
    }


def test_a_failing_write_puts_back_what_was_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = rejected_workspace(tmp_path)
    before = contents(workspace)
    original = Path.write_bytes

    def failing(self: Path, data: bytes) -> int:
        if self.name == "validation.py" and data == BASELINE_VALIDATION:
            raise OSError("disk full")
        return original(self, data)

    monkeypatch.setattr(workspace_ops.Path, "write_bytes", failing)
    with pytest.raises(OSError, match="disk full"):
        restore_changes(workspace, recorded(set()), DIFF)
    monkeypatch.undo()
    assert contents(workspace) == before
