"""The measurement of a stopped governed run's quarantined change (``evaluation/run_eval.py``).

In the Haiku pilot the quarantined patch did not apply to the restored workspace (amendment of
2026-11-11 to the pre-registration, PR #201): the kept acceptance tests already existed there and
the patch was written with CRLF line ends on Windows, so the unpatched tree was measured. The patch
is now applied, byte for byte, to the baseline, and a patch that does not apply is not measured."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
sys.path.insert(0, str(EVALUATION))

import remeasure_quarantine  # noqa: E402
import run_eval  # noqa: E402

KEPT = "tests/acceptance/test_ac.py"
PATCH = (
    "diff --git a/src/m.py b/src/m.py\n"
    "--- a/src/m.py\n"
    "+++ b/src/m.py\n"
    "@@ -1,2 +1,2 @@\n"
    " a = 1\n"
    "-b = 2\n"
    "+b = 3\n"
    f"diff --git a/{KEPT} b/{KEPT}\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    f"+++ b/{KEPT}\n"
    "@@ -0,0 +1 @@\n"
    "+def test_ac(): pass\n"
).encode()


def restored_workspace(root: Path) -> tuple[Path, str]:
    """A workspace at its baseline plus the acceptance test the harness keeps after a quarantine."""
    workspace = root / "ws"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "m.py").write_bytes(b"a = 1\nb = 2\n")
    run_eval.git(workspace, "init", "-q")
    run_eval.git(workspace, "add", "-A")
    run_eval.git(workspace, "commit", "-qm", "baseline")
    baseline = run_eval.git(workspace, "rev-parse", "HEAD").strip()
    (workspace / KEPT).parent.mkdir(parents=True)
    (workspace / KEPT).write_bytes(b"def test_ac(): pass\n")
    return workspace, baseline


@pytest.fixture
def measured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stands in for ``measure.measure``: records the tree it was given."""
    calls: list[dict[str, Any]] = []

    def fake(scenario: str, tree: Path, baseline: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"src": (tree / "src" / "m.py").read_bytes(), "kept": (tree / KEPT).is_file()})
        return {"hidden": {"passed": 1}}

    monkeypatch.setattr(run_eval, "measure", fake)
    return calls


def test_a_patch_adding_a_kept_test_applies_to_the_baseline(
    tmp_path: Path, measured: list[dict[str, Any]]
) -> None:
    workspace, baseline = restored_workspace(tmp_path)
    site = tmp_path / "site"
    site.mkdir()
    result = run_eval.measure_patch(
        "brownfield", workspace, baseline, tmp_path, tmp_path, site, PATCH
    )
    assert result["patchApply"]["exitCode"] == 0, result["patchApply"]["stderr"]
    assert result["patchApply"]["removedBeforeApply"] == ["tests/"]
    assert result["measuredOn"] == "quarantine-copy"
    assert "invalid" not in result
    assert measured == [{"src": b"a = 1\nb = 3\n", "kept": True}]
    # The patch file holds the bytes the harness printed: no CRLF, whatever the platform.
    assert (tmp_path / "quarantined.patch").read_bytes() == PATCH
    # The run's own workspace is untouched and the import path points back to it.
    assert (workspace / "src" / "m.py").read_bytes() == b"a = 1\nb = 2\n"
    assert (site / "zz_eval_workspace.pth").read_text(encoding="utf-8").strip() == str(
        workspace / "src"
    )


def test_a_patch_that_does_not_apply_is_not_measured(
    tmp_path: Path, measured: list[dict[str, Any]]
) -> None:
    workspace, baseline = restored_workspace(tmp_path)
    site = tmp_path / "site"
    site.mkdir()
    crlf = PATCH.replace(b"\n", b"\r\n")  # what a text-mode write on Windows saved
    result = run_eval.measure_patch(
        "brownfield", workspace, baseline, tmp_path, tmp_path, site, crlf
    )
    assert result["invalid"] == run_eval.QUARANTINE_NOT_APPLIED
    assert result["measuredOn"] is None
    assert result["patchApply"]["exitCode"] != 0
    assert measured == []  # the unpatched tree is never measured


def test_remeasure_undoes_the_crlf_of_a_saved_patch(
    tmp_path: Path, measured: list[dict[str, Any]]
) -> None:
    run_dir = tmp_path / "brownfield-harness-model-r1-20261111T000000Z"
    run_dir.mkdir()
    workspace, baseline = restored_workspace(run_dir)
    (run_dir / "quarantined.patch").write_bytes(PATCH.replace(b"\n", b"\r\n"))
    site = tmp_path / "site"
    site.mkdir()
    result = remeasure_quarantine.remeasure(run_dir, tmp_path, site)
    assert result["baseline"] == baseline
    assert result["measures"]["patchApply"]["exitCode"] == 0
    assert result["measures"]["patchApply"]["patchHadCrlf"] is True
    assert measured == [{"src": b"a = 1\nb = 3\n", "kept": True}]
    assert not (site / "zz_eval_workspace.pth").exists()  # left as it was found
    assert (run_dir / "quarantined.patch").read_bytes() == PATCH.replace(b"\n", b"\r\n")
