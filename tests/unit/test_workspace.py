from __future__ import annotations

from pathlib import Path

from governed_harness.runtime.workspace import WorkspaceSnapshotter


def test_snapshot_excludes_harness_state(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x=1\n")
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "state.db").write_text("ignore")
    snapshot = WorkspaceSnapshotter(tmp_path).snapshot()
    assert "src/a.py" in snapshot.files
    assert ".harness/state.db" not in snapshot.files


def test_diff_counts_additions_and_deletions(tmp_path: Path) -> None:
    file = tmp_path / "a.txt"
    file.write_text("one\ntwo\n")
    snapshotter = WorkspaceSnapshotter(tmp_path)
    before = snapshotter.snapshot()
    file.write_text("one\nthree\n")
    after = snapshotter.snapshot()
    diff = snapshotter.diff(before, after)
    assert diff.changes[0].additions == 1
    assert diff.changes[0].deletions == 1
    assert b"three" in diff.unified_diff


def test_binary_file_has_binary_marker(tmp_path: Path) -> None:
    file = tmp_path / "a.bin"
    file.write_bytes(b"\x00a")
    snapshotter = WorkspaceSnapshotter(tmp_path)
    before = snapshotter.snapshot()
    file.write_bytes(b"\x00b")
    diff = snapshotter.diff(before, snapshotter.snapshot())
    assert b"Binary files differ" in diff.unified_diff
