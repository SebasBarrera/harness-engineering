from __future__ import annotations

from pathlib import Path
from typing import Any

from governed_harness.agents.context_manifest import (
    GUIDANCE,
    build_manifest,
    key_terms,
    referenced_paths,
)
from governed_harness.domain.models import AcceptanceCriterion, Requirement, Task
from governed_harness.evidence.hashing import sha256_file


def _task(**overrides: Any) -> Task:
    data: dict[str, Any] = {
        "task_id": "TASK-1",
        "project_id": "demo",
        "title": "Compute fares for rides",
        "intent": "Compute the fare of each ride; see src/fares.py and docs/notes.md.",
        "requirements": (
            Requirement(requirement_id="REQ-1", text="The fare should include the distance."),
            Requirement(requirement_id="REQ-2", text="The fare must round to cents."),
        ),
        "acceptance_criteria": (
            AcceptanceCriterion(criterion_id="AC-1", text="Every fare is rounded to cents."),
        ),
    }
    data.update(overrides)
    return Task(**data)


def _write(root: Path, relative: str, content: str | bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _workspace(root: Path) -> Path:
    _write(root, "src/fares.py", "def fare(distance):\n    return round(distance, 2)\n")
    _write(root, "src/unrelated.py", "print('hello')\n")
    _write(root, "tests/test_fares.py", "# REQ-1 REQ-2\ndef test_fare():\n    pass\n")
    _write(root, "docs/notes.md", "Notes.\n")
    _write(root, "src/rounding.py", "# cents are rounded here\n")
    return root


# ----- key terms -----------------------------------------------------------------------------
def test_key_terms_rank_by_frequency_then_alphabet() -> None:
    # "fare" is too short, "should" and "every" are stopwords, "cents" appears twice.
    assert key_terms(_task()) == (
        "cents",
        "compute",
        "distance",
        "fares",
        "include",
        "rides",
        "round",
        "rounded",
    )


def test_key_terms_exact_values_and_limit() -> None:
    task = _task(
        title="Parser parser parser",
        requirements=(Requirement(requirement_id="REQ-1", text="lexer tokens lexer"),),
        acceptance_criteria=(AcceptanceCriterion(criterion_id="AC-1", text="tokens alpha"),),
    )
    assert key_terms(task) == ("parser", "lexer", "tokens", "alpha")
    assert key_terms(task, limit=2) == ("parser", "lexer")


# ----- referenced paths ----------------------------------------------------------------------
def test_referenced_paths_inside_workspace_only(tmp_path: Path) -> None:
    root = _workspace(tmp_path / "ws")
    _write(tmp_path, "secret.txt", "outside\n")
    task = _task(
        intent="Change ./src/fares.py, not ../secret.txt nor /etc/passwd nor src/missing.py.",
        constraints=("Keep `docs/notes.md`.",),
        metadata={"links": [{"path": "tests/test_fares.py"}], "n": 3},
    )
    assert referenced_paths(task, root) == (
        "docs/notes.md",
        "src/fares.py",
        "tests/test_fares.py",
    )


def test_referenced_paths_ignore_symlink_escape(tmp_path: Path) -> None:
    root = _workspace(tmp_path / "ws")
    outside = _write(tmp_path, "outside.py", "x = 1\n")
    (root / "link.py").symlink_to(outside)
    task = _task(intent="Look at link.py and src/fares.py.")
    assert referenced_paths(task, root) == ("src/fares.py",)


# ----- manifest ------------------------------------------------------------------------------
def test_manifest_ranking_and_reasons(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    manifest = build_manifest(
        root,
        _task(),
        changed_paths=("./src/rounding.py",),
        interface_paths=("src/fares.py",),
        lesson_paths=("docs/notes.md",),
    )
    assert manifest["schemaVersion"] == "1.0"
    assert manifest["guidance"] == GUIDANCE
    paths = [entry["path"] for entry in manifest["files"]]
    assert paths[0] == "src/fares.py"
    assert "src/unrelated.py" not in paths
    by_path = {entry["path"]: entry for entry in manifest["files"]}

    fares = by_path["src/fares.py"]
    assert fares["reasons"][:2] == ["referenced by the task", "declared interface"]
    assert "path mentions fares" in fares["reasons"]
    assert fares["digest"] == sha256_file(root / "src/fares.py")
    assert fares["sizeBytes"] == (root / "src/fares.py").stat().st_size

    test_file = by_path["tests/test_fares.py"]
    assert "names REQ-1, REQ-2" in test_file["reasons"]
    assert "test naming a requirement" in test_file["reasons"]
    assert test_file["score"] >= 45 + 10

    assert by_path["src/rounding.py"]["reasons"][0] == "changed by an earlier step"
    assert by_path["docs/notes.md"]["reasons"][:2] == [
        "referenced by the task",
        "path of an active lesson",
    ]
    scores = [(-entry["score"], entry["path"]) for entry in manifest["files"]]
    assert scores == sorted(scores)
    assert manifest["candidates"] == len(paths)
    assert manifest["omitted"] == 0
    assert manifest["totalBytes"] == sum(entry["sizeBytes"] for entry in manifest["files"])
    assert manifest["terms"] == list(key_terms(_task()))


def test_requirement_ids_are_word_bounded(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "REQ-10 and REQ-1x\n")
    _write(tmp_path, "b.txt", "see REQ-1.\n")
    manifest = build_manifest(tmp_path, _task(intent="Nothing referenced."))
    paths = [entry["path"] for entry in manifest["files"]]
    assert paths == ["b.txt"]
    assert manifest["files"][0]["reasons"] == ["names REQ-1"]
    assert manifest["files"][0]["score"] == 40


def test_id_score_is_capped(tmp_path: Path) -> None:
    requirements = tuple(
        Requirement(requirement_id=f"R{index}", text="x" * 3) for index in range(1, 9)
    )
    _write(tmp_path, "all.txt", " ".join(f"R{index}" for index in range(1, 9)) + "\n")
    manifest = build_manifest(
        tmp_path, _task(intent="Nothing referenced.", requirements=requirements)
    )
    entry = manifest["files"][0]
    assert entry["score"] == 60
    assert entry["reasons"] == ["names R1, R2, R3, R4, R5"]


def test_cap_by_file_count(tmp_path: Path) -> None:
    for index in range(5):
        _write(tmp_path, f"f{index}.txt", "REQ-1\n")
    manifest = build_manifest(tmp_path, _task(intent="Nothing referenced."), max_files=3)
    assert [entry["path"] for entry in manifest["files"]] == ["f0.txt", "f1.txt", "f2.txt"]
    assert manifest["candidates"] == 5
    assert manifest["omitted"] == 2
    assert manifest["limits"] == {"maxFiles": 3, "maxBytes": 400_000}


def test_cap_by_bytes_skips_and_continues(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "REQ-1 REQ-2\n" + "x" * 200)
    _write(tmp_path, "b.txt", "REQ-1 REQ-2\n" + "y" * 900)
    _write(tmp_path, "c.txt", "REQ-1\n" + "z" * 200)
    manifest = build_manifest(tmp_path, _task(intent="Nothing referenced."), max_bytes=600)
    assert [entry["path"] for entry in manifest["files"]] == ["a.txt", "c.txt"]
    assert manifest["omitted"] == 1
    assert manifest["totalBytes"] <= 600


def test_binary_large_and_excluded_files_are_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "src/fares.py", "REQ-1\n")
    _write(tmp_path, "blob.bin", b"REQ-1\x00\x01\x02")
    _write(tmp_path, "huge.txt", "REQ-1\n" + "a" * (1024 * 1024 + 1))
    _write(tmp_path, "node_modules/fares.js", "REQ-1\n")
    _write(tmp_path, ".git/fares", "REQ-1\n")
    _write(tmp_path, "custom/fares.txt", "REQ-1\n")
    outside = _write(tmp_path.parent / f"{tmp_path.name}-outside", "fares.py", "REQ-1\n")
    (tmp_path / "linked").symlink_to(outside.parent, target_is_directory=True)
    (tmp_path / "linked_fares.py").symlink_to(outside)
    manifest = build_manifest(
        tmp_path, _task(intent="Nothing referenced."), excludes={"node_modules", ".git", "custom"}
    )
    assert [entry["path"] for entry in manifest["files"]] == ["src/fares.py"]


def test_default_excludes_apply(tmp_path: Path) -> None:
    _write(tmp_path, ".venv/lib/fares.py", "REQ-1\n")
    _write(tmp_path, "app.py", "REQ-1\n")
    manifest = build_manifest(tmp_path, _task(intent="Nothing referenced."))
    assert [entry["path"] for entry in manifest["files"]] == ["app.py"]


def test_empty_when_nothing_matches(tmp_path: Path) -> None:
    _write(tmp_path, "readme.txt", "hello\n")
    manifest = build_manifest(tmp_path, _task(intent="Nothing referenced."))
    assert manifest["files"] == []
    assert manifest["candidates"] == 0
    assert manifest["totalBytes"] == 0


def test_manifest_is_deterministic(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    first = build_manifest(root, _task(), changed_paths=("src/rounding.py",))
    second = build_manifest(root, _task(), changed_paths=("src/rounding.py",))
    assert first == second
    assert first["digest"].startswith("sha256:")
    (root / "src/fares.py").write_text("changed\n", encoding="utf-8")
    third = build_manifest(root, _task(), changed_paths=("src/rounding.py",))
    assert third["digest"] != first["digest"]
