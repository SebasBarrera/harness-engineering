from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_default_workflow_has_exact_normative_phase_order() -> None:
    workflow = yaml.safe_load((ROOT / "workflows" / "default.yaml").read_text(encoding="utf-8"))
    ids = [phase["id"] for phase in workflow["phases"]]
    assert ids == [
        "INTENT",
        "DISCOVERY",
        "SPECIFICATION",
        "PLANNING",
        "IMPLEMENTATION",
        "VERIFICATION",
        "INDEPENDENT_REVIEW",
        "DECISION",
        "CLOSURE",
    ]
    assert len(ids) == len(set(ids))


def test_no_phase_can_grant_git_push_by_default() -> None:
    workflow = yaml.safe_load((ROOT / "workflows" / "default.yaml").read_text(encoding="utf-8"))
    assert all("git.push" not in phase["allowedCapabilities"] for phase in workflow["phases"])
