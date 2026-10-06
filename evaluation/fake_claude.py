#!/usr/bin/env python3
"""Deterministic stand-in for ``claude -p ... --output-format json`` (dry runs, no model call).

It accepts the command line of ``agentlib._command`` and prints one result object in the shape
Claude Code prints (``result``, ``is_error``, ``subtype``, ``usage``, ``total_cost_usd``,
``modelUsage``), with zero usage and zero cost. What it answers depends on the prompt:

* the read-only calls of the harness (protocol 1.1: clarify, review, panel reviewers, plan,
  acceptance, locate, architecture survey and advice) get a valid ``result`` object, chosen so that
  every wait of the simulated person is exercised once (one clarification question per task until
  it has been answered, acceptance tests, a plan, architecture options or layers);
* the simulated product owner gets an answers file for every question id in its prompt;
* anything else is an implementation: the reference solution of the scenario found in the working
  directory is copied in, with one test that names every requirement and criterion id.

It is used by ``run_eval.py --dry-run`` and by ``selftest.py``; it never calls a model.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REFERENCE = HERE / "reference"


def _arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def _request(prompt: str) -> dict[str, Any]:
    match = re.search(r"```json\n(.*?)\n```", prompt, re.DOTALL)
    try:
        return json.loads(match.group(1)) if match else {}
    except ValueError:
        return {}


def _scenario(cwd: Path) -> str | None:
    if (cwd / "src" / "itsdangerous").is_dir():
        return "brownfield"
    spec = (cwd / "SPEC.md").read_text(encoding="utf-8") if (cwd / "SPEC.md").is_file() else ""
    if "Inventory library" in spec:
        return "inventory"
    if "`alerts`" in spec:
        return "security"
    if "shipping" in spec:
        return "greenfield"
    return None


def _mode(cwd: Path) -> str:
    """The dry run's fault, written by run_eval.py --fake-mode next to the workspace."""
    path = cwd.parent / "fake-mode"
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


PACKAGE = {
    "greenfield": "shipping",
    "security": "alerts",
    "brownfield": "itsdangerous",
    "inventory": "inventory",
}


def _ids(text: str) -> list[str]:
    return sorted(set(re.findall(r"\b(?:req|ac)_[A-Za-z0-9_]+", text)))


def _implement(cwd: Path, prompt: str) -> str:
    scenario = _scenario(cwd)
    if scenario == "brownfield":
        shutil.copy2(
            REFERENCE / "brownfield-itsdangerous-encoding.py", cwd / "src/itsdangerous/encoding.py"
        )
    elif scenario == "inventory":
        source = HERE / "longitudinal" / "reference" / "inventory"
        shutil.copytree(source, cwd / "src" / "inventory", dirs_exist_ok=True)
    elif scenario in ("greenfield", "security"):
        source = REFERENCE / f"greenfield-{'shipping' if scenario == 'greenfield' else 'alerts'}"
        for package in source.iterdir():
            shutil.copytree(package, cwd / "src" / package.name, dirs_exist_ok=True)
    else:
        return "Nothing to implement: unknown scenario."
    package = PACKAGE[scenario]
    names = _ids(prompt) or ["req_spec"]
    tests = cwd / "tests"
    tests.mkdir(exist_ok=True)
    body = [f"import {package}", "", ""]
    for name in names:
        body += [
            f"def test_{name}_is_importable() -> None:",
            f"    assert {package} is not None",
            "",
            "",
        ]
    (tests / "test_fake_dry_run.py").write_text(
        "\n".join(body).rstrip("\n") + "\n", encoding="utf-8"
    )
    if _mode(cwd) == "broken":
        # A change whose own test fails: verification fails, the corrections change nothing and
        # the run stops (stop the line quarantines it).
        (tests / "test_fake_broken.py").write_text(
            "def test_broken() -> None:\n    assert False\n", encoding="utf-8"
        )
    return f"Applied the reference solution of {scenario} and one test per id ({len(names)})."


def _call(prompt: str, cwd: Path) -> dict[str, Any] | None:
    """The ``result`` object of a read-only call, or None when the prompt is not one."""
    request = _request(prompt)
    task = request.get("task") or {}
    if "Review the task below before any work starts" in prompt:
        answered = bool(request.get("clarifications")) or "clarification" in json.dumps(task)
        if answered:
            return {"questions": []}
        return {
            "questions": [
                {
                    "category": "edge-cases",
                    "target": "task",
                    "text": "Which result is expected for the smallest valid input?",
                }
            ]
        }
    if '"verdict": "PASS"' in prompt:
        if _mode(cwd) == "review-fail":
            # One blocking finding whose evidence quotes the first line the change added.
            diff = (request.get("slice") or {}).get("diff") or ""
            path, line, text = None, 0, ""
            for row in diff.splitlines():
                if row.startswith("+++ "):
                    path, line = row[6:] if row.startswith("+++ b/") else None, 0
                elif row.startswith("@@"):
                    line = int(row.split("+", 1)[1].split(" ", 1)[0].split(",")[0]) - 1
                elif row.startswith("+") and path:
                    line, text = line + 1, row[1:]
                    if text.strip():
                        break
                elif not row.startswith("-"):
                    line += 1
            if path and text.strip():
                finding = {
                    "file": path,
                    "side": "new",
                    "line": line,
                    "rule": "quality.dry-run",
                    "severity": "error",
                    "issue": "Dry run: a blocking finding.",
                    "evidence": text.strip(),
                }
                return {"verdict": "FAIL", "findings": [finding], "summary": "Dry run: one error."}
        return {"verdict": "PASS", "findings": [], "summary": "Dry run: no finding."}
    if "You are an independent reviewer" in prompt:
        return {"findings": []}
    if "Split the task below into ordered sub-tasks" in prompt:
        return {
            "subtasks": [
                {
                    "title": "Whole task",
                    "requirements": [
                        r.get("requirement_id") for r in task.get("requirements") or []
                    ],
                    "criteria": [
                        c.get("criterion_id") for c in task.get("acceptance_criteria") or []
                    ],
                    "constraints": [],
                }
            ]
        }
    if "Write acceptance tests for the task below" in prompt:
        package = PACKAGE.get(_scenario(cwd) or "", "")
        directory = request.get("directory") or "tests/acceptance"
        criteria = [c.get("criterion_id") for c in task.get("acceptance_criteria") or []] or ["ac"]
        content = "import importlib\n" + "".join(
            f"\n\ndef test_{cid}_package_exists() -> None:\n    assert importlib.import_module({package!r})\n"
            for cid in criteria
        )
        name = f"{directory}/test_acceptance_{criteria[0]}.py"  # one file per task: no overwrite
        return {"tests": [{"path": name, "content": content}]}
    if "Find where the task below must be implemented" in prompt:
        return {"locations": [], "questions": []}
    if "Survey the architecture of the existing project" in prompt:
        return {
            "style": "layered",
            "summary": "Dry run: one source layer.",
            "layers": [{"name": "source", "paths": ["src/**"], "modules": []}],
            "allow": {"source": []},
        }
    if "is new. From the task below" in prompt:
        option = {
            "benefits": ["simple"],
            "costs": ["none"],
            "fit": "small library",
            "layers": [{"name": "package", "paths": ["src/**"], "modules": []}],
            "allow": {"package": []},
        }
        return {
            "options": [
                {
                    **option,
                    "id": "single",
                    "style": "layered",
                    "title": "One package",
                    "recommended": True,
                },
                {
                    **option,
                    "id": "modular",
                    "style": "modular-monolith",
                    "title": "Modules",
                    "recommended": False,
                },
            ]
        }
    return None


def _product_owner(prompt: str) -> str | None:
    if not prompt.startswith("You are the product owner"):
        return None
    ids = sorted(set(re.findall(r"^- (Q-\d+):", prompt, re.MULTILINE)))
    lines = ["```yaml", "answers:"]
    for qid in ids:
        lines += [
            f"  {qid}: |",
            "    The smallest valid input returns the documented result; invalid input raises the documented error.",
        ]
    lines += [
        "addCriteria:",
        "  - |",
        "    Running python -m pytest -q exits with code 0.",
        "```",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    prompt = _arg(argv, "-p") or sys.stdin.read()
    model = _arg(argv, "--model") or "fake"
    cwd = Path.cwd()
    text = _product_owner(prompt)
    if text is None:
        result = _call(prompt, cwd)
        if result is not None:
            text = json.dumps({"status": "PASSED", "summary": "Dry-run answer.", "result": result})
        else:
            text = _implement(cwd, prompt)
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": text,
                "num_turns": 1,
                "duration_api_ms": 0,
                "total_cost_usd": 0.0,
                "session_id": str(uuid.uuid4()),
                "usage": usage,
                "modelUsage": {model: {"inputTokens": 0, "outputTokens": 0, "costUSD": 0.0}},
                "permission_denials": [],
                "terminal_reason": "completed",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
