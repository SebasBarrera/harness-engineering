"""Project-defined profiles, validators and interpreter (``toolchain``)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.models import Finding, GateEvaluation, ValidationResult

TASK = (
    "title: Implement threshold discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - A subtotal of 100 with a ten percent rate returns 90.\n"
    "implementation:\n"
    "  mode: patch\n"
    "  patches:\n"
    "    - path: src/sample/pricing.py\n"
    "      operation: replace\n"
    "      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
    "    - path: tests/test_pricing.py\n"
    "      operation: append\n"
    "      content: |\n"
    "\n"
    "        def test_at_threshold() -> None:\n"
    "            assert apply_discount(100, 100, 0.1) == 90\n"
)

SARIF_TOOL = """\
import json, os, sys
print(json.dumps({"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "scanner"}},
  "results": [
    {"ruleId": "S1", "level": "error", "message": {"text": "unsafe call"},
     "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/sample/pricing.py"},
       "region": {"startLine": 2}}}]},
    {"ruleId": "S2", "level": "warning", "message": {"text": "style " + os.environ.get("SCANNER_MODE", "unset")},
     "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/sample/pricing.py"},
       "region": {"startLine": 1}}}]}]}]}))
sys.exit(1)
"""


def write_task(path: Path, content: str) -> Path:
    task = path / "task-input.yaml"
    task.write_text(content, encoding="utf-8")
    return task


def update_config(workspace: Path, **sections: Any) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.update(sections)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def resolve(workspace: Path) -> Any:
    return ConfigurationResolver().resolve(workspace)


def test_project_validator_with_parser_severity_and_pass_env(
    python_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (python_workspace / "tools").mkdir()
    (python_workspace / "tools" / "scan.py").write_text(SARIF_TOOL, encoding="utf-8")
    monkeypatch.setenv("SCANNER_MODE", "strict-mode-value")
    update_config(
        python_workspace,
        toolchain={
            "validators": [
                {
                    "id": "project.scanner",
                    "command": ["python", "tools/scan.py"],
                    "mandatory": False,
                    "parser": "sarif",
                    "severity": {"error": "HIGH", "warning": "MEDIUM"},
                    "failureSeverity": "LOW",
                    "passEnv": ["SCANNER_MODE"],
                }
            ]
        },
    )
    resolved = resolve(python_workspace)
    ids = [item.validator_id for item in resolved.effective_validators]
    assert ids[-1] == "project.scanner" and "python.pytest" in ids
    assert any(
        rule.capability == "process.execute" and "python tools/scan.py" in rule.scope
        for rule in resolved.effective_capabilities
    )
    application = HarnessApplication()
    task = application.create_task(python_workspace, write_task(python_workspace.parent, TASK))
    run = application.start_run(python_workspace, task.task_id).execution_id
    with application._services(python_workspace) as services:
        findings = [
            item
            for item in services.state.list("finding", Finding, execution_id=run)
            if item.validator_id == "project.scanner"
        ]
        validation = [
            item
            for item in services.state.list("validation", ValidationResult, execution_id=run)
            if item.validator_id == "project.scanner"
        ][-1]
        gates = services.state.list("gate", GateEvaluation, execution_id=run)
    by_rule = {item.rule_id: item for item in findings}
    assert by_rule["project.scanner.failed"].severity.value == "LOW"
    assert by_rule["project.scanner.S1"].severity.value == "HIGH"
    assert by_rule["project.scanner.S1"].location is not None
    assert by_rule["project.scanner.S1"].location.start_line == 2
    assert by_rule["project.scanner.S2"].severity.value == "MEDIUM"
    # The variable reached the command and its value was redacted from the stored output.
    assert "style <REDACTED_ENV>" in by_rule["project.scanner.S2"].message
    assert validation.mandatory is False
    assert gates[-1].status.value == "FAILED"  # a HIGH finding blocks under the default policy


def test_project_validator_replaces_a_profile_validator(python_workspace: Path) -> None:
    update_config(
        python_workspace,
        toolchain={
            "validators": [
                {"id": "python.pytest", "command": ["python", "-m", "pytest", "-q", "tests"]}
            ]
        },
    )
    resolved = resolve(python_workspace)
    pytest_definition = [
        item for item in resolved.effective_validators if item.validator_id == "python.pytest"
    ]
    assert len(pytest_definition) == 1
    assert pytest_definition[0].command == ("python", "-m", "pytest", "-q", "tests")


def test_project_profile_is_loaded_detected_and_run(python_workspace: Path) -> None:
    (python_workspace / "harness-profiles").mkdir()
    (python_workspace / "harness-profiles" / "docs.yaml").write_text(
        yaml.safe_dump(
            {
                "profileVersion": "1.0",
                "profileId": "project_docs",
                "technology": "docs",
                "detectors": [{"marker": "docs.toml", "weight": 0.9}],
                "defaultValidators": ["docs.check"],
                "validators": [{"id": "docs.check", "command": ["python", "-c", "print('ok')"]}],
                "capabilities": {"process.execute": ["python"], "filesystem.read": ["**"]},
            }
        ),
        encoding="utf-8",
    )
    (python_workspace / "docs.toml").write_text("", encoding="utf-8")
    update_config(
        python_workspace,
        profiles=["auto"],
        toolchain={"profilePaths": ["harness-profiles"], "profileDetection": "all"},
    )
    resolved = resolve(python_workspace)
    assert [item.profile_id for item in resolved.profiles] == ["project_docs", "python_default"]
    assert any(path.endswith("docs.yaml") for path in resolved.source_files)
    assert "docs.check" in [item.validator_id for item in resolved.effective_validators]
    application = HarnessApplication()
    task = application.create_task(python_workspace, write_task(python_workspace.parent, TASK))
    run = application.start_run(python_workspace, task.task_id).execution_id
    status = application.status(python_workspace, run)
    assert status["execution"]["currentPhase"] == "DECISION"
    with application._services(python_workspace) as services:
        ran = {
            item.validator_id
            for item in services.state.list("validation", ValidationResult, execution_id=run)
        }
    assert ran >= {"docs.check", "python.pytest"}


def test_best_detection_stays_the_default(python_workspace: Path) -> None:
    (python_workspace / "package.json").write_text("{}", encoding="utf-8")
    update_config(python_workspace, profiles=["auto"])
    assert [item.profile_id for item in resolve(python_workspace).profiles] == ["python_default"]
    update_config(python_workspace, toolchain={"profileDetection": "all"})
    assert [item.profile_id for item in resolve(python_workspace).profiles] == [
        "python_default",
        "node_default",
    ]


def test_project_profile_cannot_reuse_a_builtin_id(python_workspace: Path) -> None:
    (python_workspace / "p.yaml").write_text(
        yaml.safe_dump(
            {
                "profileVersion": "1.0",
                "profileId": "python",
                "technology": "python",
                "detectors": [],
            }
        ),
        encoding="utf-8",
    )
    update_config(python_workspace, toolchain={"profilePaths": ["p.yaml"]})
    with pytest.raises(ConfigurationError, match="built-in profile id"):
        resolve(python_workspace)


def test_interpreter_auto_uses_the_local_virtual_environment(python_workspace: Path) -> None:
    interpreter = python_workspace / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    update_config(python_workspace, toolchain={"interpreter": "auto"})
    resolved = resolve(python_workspace)
    commands = {item.validator_id: item.command for item in resolved.effective_validators}
    assert commands["python.pytest"] == (str(interpreter.resolve()), "-m", "pytest", "-q")
    assert any(
        str(interpreter.resolve()) in rule.scope
        for rule in resolved.effective_capabilities
        if rule.capability == "process.execute"
    )


@pytest.mark.skipif(os.name == "nt", reason="a POSIX shell script stands in for uv")
def test_interpreter_auto_uses_uv_with_a_lock_file(
    python_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    (python_workspace / "uv.lock").write_text("", encoding="utf-8")
    update_config(python_workspace, toolchain={"interpreter": "auto"})
    commands = {
        item.validator_id: item.command for item in resolve(python_workspace).effective_validators
    }
    assert commands["python.pytest"] == ("uv", "run", "--no-sync", "python", "-m", "pytest", "-q")


def test_without_toolchain_the_configuration_digest_is_unchanged(python_workspace: Path) -> None:
    before = resolve(python_workspace).model_dump(mode="json", by_alias=True)
    update_config(python_workspace, toolchain={})
    after = resolve(python_workspace).model_dump(mode="json", by_alias=True)
    after["project"].pop("toolchain", None)
    assert json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)
