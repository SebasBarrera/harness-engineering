from __future__ import annotations

import subprocess
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


def test_diff_lines_are_not_followed_by_empty_lines(tmp_path: Path) -> None:
    file = tmp_path / "a.txt"
    file.write_text("one\ntwo\n")
    snapshotter = WorkspaceSnapshotter(tmp_path)
    before = snapshotter.snapshot()
    file.write_text("one\nthree\n")
    diff = snapshotter.diff(before, snapshotter.snapshot())
    assert diff.unified_diff == (b"--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n")


def test_diff_is_a_patch_git_applies(tmp_path: Path) -> None:
    """The ChangeSet diff is a valid unified diff: git apply accepts it on the baseline and
    reproduces the changed workspace (modified, added and deleted files, a last line without a
    newline and a form feed inside a line)."""
    original = {
        "src/kept.py": "a = 1\nb = 2\nc = 3\n",
        "src/gone.py": "x = 1\n",
        "notes.txt": "first\nno newline at end",
        "page.txt": "title\x0cfooter\nend\n",
    }
    changed = {
        "src/kept.py": "a = 1\nb = 20\nc = 3\nd = 4\n",
        "src/new.py": "def f() -> int:\n    return 1\n",
        "notes.txt": "first\nstill no newline",
        "page.txt": "title\x0cfooter\nend\nmore\n",
    }
    # Bytes, not text: newline translation on Windows would change the content.
    for relative, text in original.items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_bytes(text.encode("utf-8"))
    snapshotter = WorkspaceSnapshotter(tmp_path)
    before = snapshotter.snapshot()
    (tmp_path / "src" / "gone.py").unlink()
    for relative, text in changed.items():
        (tmp_path / relative).write_bytes(text.encode("utf-8"))
    after = snapshotter.snapshot()
    diff = snapshotter.diff(before, after)
    assert {change.status for change in diff.changes} == {"ADDED", "DELETED", "MODIFIED"}

    # Restore the baseline and apply the recorded diff to it.
    (tmp_path / "src" / "new.py").unlink()
    for relative, text in original.items():
        (tmp_path / relative).write_bytes(text.encode("utf-8"))
    patch = tmp_path.parent / f"{tmp_path.name}.diff"
    patch.write_bytes(diff.unified_diff)
    check = subprocess.run(
        ["git", "apply", "--check", str(patch)], cwd=tmp_path, capture_output=True, text=True
    )
    assert check.returncode == 0, check.stderr
    subprocess.run(["git", "apply", str(patch)], cwd=tmp_path, check=True)
    assert not (tmp_path / "src" / "gone.py").exists()
    for relative, text in changed.items():
        assert (tmp_path / relative).read_bytes() == text.encode("utf-8")


def test_binary_file_has_binary_marker(tmp_path: Path) -> None:
    file = tmp_path / "a.bin"
    file.write_bytes(b"\x00a")
    snapshotter = WorkspaceSnapshotter(tmp_path)
    before = snapshotter.snapshot()
    file.write_bytes(b"\x00b")
    diff = snapshotter.diff(before, snapshotter.snapshot())
    assert b"Binary files differ" in diff.unified_diff
