"""Putting a quarantined ChangeSet back for ``run continue`` (governance.stopTheLine: restore,
#193): only intact content, only over the baseline, nothing written on a refusal."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from governed_harness.evidence.artifact_store import LocalArtifactStore
from governed_harness.evidence.hashing import sha256_bytes
from governed_harness.orchestration.stop_line import StopLine
from governed_harness.runtime.workspace import WorkspaceSnapshotter

BASELINE = b"def f() -> int:\n    return 1\n"
CHANGED = b"def f() -> int:\n    return 2\n"
NO_RUN: Any = None  # the fake host ignores the run


def stop_line(tmp_path: Path) -> tuple[StopLine, Path, LocalArtifactStore]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "f.py").write_bytes(BASELINE)
    (workspace / "gone.py").write_bytes(BASELINE)
    baseline = WorkspaceSnapshotter(workspace).snapshot()
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    host: Any = SimpleNamespace(
        s=SimpleNamespace(paths=SimpleNamespace(workspace=workspace), artifacts=artifacts),
        baseline_snapshot=lambda execution: baseline,
    )
    return StopLine(host), workspace, artifacts


def record(artifacts: LocalArtifactStore, content: bytes = CHANGED) -> dict[str, Any]:
    ref = artifacts.put(content, redact=False).uri
    return {
        "restoredPaths": ["f.py", "gone.py", "new.py"],
        "quarantinedContents": [
            {"path": "f.py", "digest": sha256_bytes(CHANGED), "contentRef": ref},
            {"path": "gone.py", "digest": None},
            {"path": "new.py", "digest": sha256_bytes(CHANGED), "contentRef": ref},
        ],
    }


def test_intact_content_over_the_baseline_is_put_back(tmp_path: Path) -> None:
    line, workspace, artifacts = stop_line(tmp_path)
    planned, problems = line._plan_reapply(NO_RUN, record(artifacts))
    assert problems == []
    assert line._write(planned) == []
    assert (workspace / "f.py").read_bytes() == CHANGED
    assert (workspace / "new.py").read_bytes() == CHANGED
    assert not (workspace / "gone.py").exists()


def test_a_path_already_holding_the_quarantined_content_is_accepted(tmp_path: Path) -> None:
    line, workspace, artifacts = stop_line(tmp_path)
    (workspace / "f.py").write_bytes(CHANGED)
    planned, problems = line._plan_reapply(NO_RUN, record(artifacts, b"[REDACTED]\n"))
    # f.py is not written (nor its stored content read); new.py still needs intact content.
    assert [path for path, _target, _data in planned] == ["gone.py"]
    assert problems == ["new.py: quarantined content missing or not matching its digest"]
    assert line._rewritten(NO_RUN, record(artifacts)) == []


def test_a_new_attempt_over_a_restored_path_supersedes_the_quarantine(tmp_path: Path) -> None:
    line, workspace, artifacts = stop_line(tmp_path)
    (workspace / "f.py").write_bytes(b"a new attempt\n")
    assert line._rewritten(NO_RUN, record(artifacts)) == ["f.py"]
    # Never written over, even if asked to plan it.
    _planned, problems = line._plan_reapply(NO_RUN, record(artifacts))
    assert problems == ["f.py: changed since the quarantine"]


def test_content_that_does_not_match_its_digest_is_refused(tmp_path: Path) -> None:
    line, _workspace, artifacts = stop_line(tmp_path)
    # A redacted or substituted artifact holds other bytes than the file had.
    _planned, problems = line._plan_reapply(NO_RUN, record(artifacts, b"[REDACTED]\n"))
    assert problems == [
        "f.py: quarantined content missing or not matching its digest",
        "new.py: quarantined content missing or not matching its digest",
    ]


def test_a_path_without_recorded_content_is_refused(tmp_path: Path) -> None:
    line, _workspace, artifacts = stop_line(tmp_path)
    old = record(artifacts)
    old["quarantinedContents"] = old["quarantinedContents"][1:]
    _planned, problems = line._plan_reapply(NO_RUN, old)
    assert problems == ["f.py: no recorded content"]
