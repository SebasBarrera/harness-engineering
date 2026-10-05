from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from governed_harness.application import HarnessApplication

# Fixture repositories must not inherit the developer's Git configuration: global commit
# signing or a global core.hooksPath would make the baseline commit fail or prompt.
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
GIT_ISOLATION = ("-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}")


@pytest.fixture(autouse=True)
def _isolated_git_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Git commands the harness itself runs (the closure commit, decider identity) must not
    read the developer's global or system configuration either."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture(autouse=True)
def _anchor_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    # governance.chainAnchor: file (written by init) keeps chain anchors under the user's data
    # directory; tests keep them in a temporary one.
    monkeypatch.setenv("HARNESS_ANCHOR_DIR", str(tmp_path_factory.mktemp("anchors")))


@pytest.fixture(autouse=True)
def _state_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    # runtime.stateDir: auto (written by init, #55) keeps the run registry under the user's data
    # directory; tests keep it in a temporary one.
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path_factory.mktemp("state")))


AGENT_RESULTS_KEYS: dict[str, tuple[str, ...]] = {
    "intake": ("ambiguityReview", "clarifyAgent", "validateAnswers", "projectSetup"),
    "verification": (
        "interface",
        "architecture",
        "securityPatterns",
        "constraints",
        "ratchet",
        "invariants",
        "differential",
        "weakenedControls",
        "testQuality",
        "secrets",
        "sarif",
        "riskFactors",
        "acceptanceTests",
        "principles",
    ),
    "review": ("agentReview", "reviewer", "structuredChanges"),
    "runtime": ("gateContract", "reproduceFirst"),
    "governance": ("stopTheLine", "phasePermissions"),
}
AGENT_RESULTS_SECTIONS = (
    "planning",
    "context",
    "budget",
    "memory",
    "agentRouting",
    "standards",
    "testing",
    "architecture",
)
LADDER_KEYS: dict[str, tuple[str, ...]] = {
    "workspace": ("isolation",),
    "runtime": ("stateDir",),
    "intake": ("operationalContract", "interruptions"),
    "verification": ("ladder", "probes", "mutation"),
    "review": ("manualChecklist",),
    "toolchain": ("extendedProfiles",),
    "delivery": ("stage", "push", "pullRequest", "comment", "forge"),
}
LADDER_SECTIONS = ("environment", "instructions")
FRICTION_SECTIONS = ("friction", "metrics")
"""Wave 8 (#58): tests of the fast lane, the pre-authorised approval, the change types, the
plan-approval checkpoint and the metrics settings turn on what they need."""
API_SECTIONS = ("api",)
"""API authentication (#18): fixture projects serve the API without a token, as in 1.0.0."""


def without_agent_results(root: Path) -> None:
    """Remove the agent-results settings (#37-#44, #52), the verification-ladder settings
    (#55), the friction settings (#58) and the API authentication (#18) that ``harness init``
    writes, so a fixture project behaves as before them; tests of those settings turn on what
    they need."""
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for section, keys in (*AGENT_RESULTS_KEYS.items(), *LADDER_KEYS.items()):
        for key in keys:
            config.get(section, {}).pop(key, None)
    for section in (
        *AGENT_RESULTS_SECTIONS,
        *LADDER_SECTIONS,
        *FRICTION_SECTIONS,
        *API_SECTIONS,
    ):
        config.pop(section, None)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


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
    without_agent_results(root)
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
    without_agent_results(root)
    return root


def write_task(path: Path, content: str) -> Path:
    task = path / "task-input.yaml"
    task.write_text(content, encoding="utf-8")
    return task
