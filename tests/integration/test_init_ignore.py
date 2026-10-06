"""``harness init`` leaves the tree of an existing repository clean (#86): its ignore entry goes
to the repository's ``info/exclude``, so the first run reports no uncommitted change caused by
init."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from governed_harness.cli.main import app
from governed_harness.orchestration.ladder_environment import dirty_paths
from tests.conftest import GIT_ENV, GIT_ISOLATION, git_init


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *GIT_ISOLATION, *args],
        cwd=root,
        check=True,
        env=GIT_ENV,
        capture_output=True,
        text=True,
    ).stdout


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "brownfield"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1'\n")
    (root / ".gitignore").write_text("dist/\n", encoding="utf-8")
    git_init(root)
    return root


def _init(root: Path, *extra: str) -> dict[str, object]:
    result = CliRunner().invoke(app, ["--json", "init", "--path", str(root), *extra])
    assert result.exit_code == 0, result.output
    value: dict[str, object] = json.loads(result.stdout)
    return value


def test_init_in_a_repository_leaves_the_tree_clean(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    payload = _init(root)
    assert (root / ".gitignore").read_text(encoding="utf-8") == "dist/\n"
    exclude = root / ".git" / "info" / "exclude"
    assert ".harness/" in exclude.read_text(encoding="utf-8").splitlines()
    assert payload["ignore"] == {
        "file": ".git/info/exclude",
        "entry": ".harness/",
        "status": "added",
    }
    assert "gitignore" not in payload
    assert _git(root, "status", "--porcelain", "--untracked-files=normal") == ""
    # The dirty-tree check of the first run finds nothing init caused.
    assert dirty_paths(root) == []
    # Git ignores the state of the harness through the exclude file.
    _git(root, "check-ignore", "-q", ".harness/state.db")


def test_a_second_init_finds_the_entry_present(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _init(root)
    again = _init(root, "--force")
    assert again["ignore"] == {
        "file": ".git/info/exclude",
        "entry": ".harness/",
        "status": "present",
    }
    exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count(".harness/") == 1


def test_an_entry_already_in_gitignore_is_left_alone(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / ".gitignore").write_text("dist/\n.harness/\n", encoding="utf-8")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-qm", "ignore")
    payload = _init(root)
    assert payload["ignore"] == {"file": ".gitignore", "entry": ".harness/", "status": "present"}
    assert ".harness/" not in (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")


def test_the_gitignore_can_still_be_chosen(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    payload = _init(root, "--ignore-file", "gitignore")
    assert payload["gitignore"] == "added"
    assert payload["ignore"] == {"file": ".gitignore", "entry": ".harness/", "status": "added"}
    assert (root / ".gitignore").read_text(encoding="utf-8") == "dist/\n.harness/\n"


def test_outside_a_repository_init_writes_the_gitignore(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1'\n")
    payload = _init(root)
    assert payload["gitignore"] == "created"
    assert payload["ignore"] == {"file": ".gitignore", "entry": ".harness/", "status": "created"}
    assert (root / ".gitignore").read_text(encoding="utf-8") == ".harness/\n"
