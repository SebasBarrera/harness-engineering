"""The generated CLI reference (``scripts/gen_cli_docs.py``) documents every command a person can
run, including a group that runs on its own (#157: ``harness review --run``)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_cli_docs.py"


def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_cli_docs", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_group_that_runs_without_a_subcommand_has_its_own_section() -> None:
    text = generator().render()
    section = text.split("\n## harness review\n", 1)
    assert len(section) == 2, "harness review has no section"
    body = section[1].split("\n## ", 1)[0]
    assert "`--run`" in body
    assert "| [`harness review`](#harness-review) |" in text
