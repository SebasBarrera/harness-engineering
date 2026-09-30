from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from governed_harness.application import HarnessApplication


# Fixture repositories must not inherit the developer's Git configuration: global commit
# signing or a global core.hooksPath would make the baseline commit fail or prompt.
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
GIT_ISOLATION = ("-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}")


def git_init(path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *GIT_ISOLATION, *args], cwd=path, check=True, env=GIT_ENV)

    git("init", "-q")
    git("config", "user.email", "fixture@example.com")
    git("config", "user.name", "Fixture")
    git("add", ".")
    git("commit", "-qm", "baseline")


@pytest.fixture
def python_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "python-project"
    (root / "src" / "sample").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "sample" / "__init__.py").write_text(
        "from .pricing import apply_discount\n", encoding="utf-8"
    )
    (root / "src" / "sample" / "pricing.py").write_text(
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "    return subtotal\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_pricing.py").write_text(
        "from sample import apply_discount\n\n"
        "def test_below_threshold() -> None:\n"
        "    assert apply_discount(99, 100, 0.1) == 99\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        "[build-system]\nrequires=[]\nbuild-backend='setuptools.build_meta'\n\n"
        "[project]\nname='sample-fixture'\nversion='0.1.0'\n\n"
        "[tool.pytest.ini_options]\ntestpaths=['tests']\npythonpath=['src']\n",
        encoding="utf-8",
    )
    git_init(root)
    HarnessApplication().init(root)
    return root


@pytest.fixture
def node_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "node-project"
    (root / "src").mkdir(parents=True)
    (root / "test").mkdir()
    (root / "src" / "resolve-config.js").write_text(
        "export function resolveConfig(defaults, repository, task) {\n"
        "  return { ...defaults, ...task, ...repository };\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "test" / "resolve-config.test.js").write_text(
        "import assert from 'node:assert/strict';\n"
        "import test from 'node:test';\n"
        "import { resolveConfig } from '../src/resolve-config.js';\n\n"
        "test('repository overrides defaults', () => {\n"
        "  assert.deepEqual(resolveConfig({mode:'safe'}, {mode:'strict'}, {}), {mode:'strict'});\n"
        "});\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        "{\n"
        '  "name": "node-fixture",\n'
        '  "version": "0.1.0",\n'
        '  "private": true,\n'
        '  "type": "module",\n'
        '  "scripts": {\n'
        '    "test": "node --test",\n'
        '    "lint": "node --check src/resolve-config.js",\n'
        '    "typecheck": "node --check src/resolve-config.js"\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    git_init(root)
    HarnessApplication().init(root)
    return root


def write_task(path: Path, content: str) -> Path:
    task = path / "task-input.yaml"
    task.write_text(content, encoding="utf-8")
    return task
