#!/usr/bin/env python3
"""Deterministic stand-in for ``claude -p ... --output-format json`` (dry runs, no model call).

It accepts the command line of ``agentlib._command`` and prints one result object in the shape
Claude Code prints (``result``, ``is_error``, ``subtype``, ``usage``, ``total_cost_usd``,
``modelUsage``), with zero usage and zero cost. What it answers depends on the prompt:

* the read-only calls of the harness (protocol 1.1: clarify, review, panel reviewers, plan,
  acceptance, locate, architecture survey and advice) get a valid ``result`` object, chosen so that
  every wait of the simulated person is exercised once (one clarification question per task until
  it has been answered, acceptance tests, a plan, architecture options or layers);
* the simulated product owner gets an answers file for every question id in its prompt, and
  REVISE for a dispute of frozen acceptance tests;
* anything else is an implementation: the reference solution of the scenario found in the working
  directory is copied in, with one test that names every requirement and criterion id.

Sessions and the turn cap (amendment A3) are simulated as Claude Code handles them: ``--session-id``
keeps a session (a marker file in ``fake-sessions`` next to the workspace), ``--resume`` of a session
it does not have fails with "No conversation found" and no turn, and the result names the session.
In the ``turn-limit`` mode the first implement call that carries ``--max-turns`` stops on the cap
(``subtype: error_max_turns``, ``terminal_reason: max_turns``, no change), and later calls work.

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
    if "Biblioteca de inventario" in spec:
        return "inventory"
    if "`alerts`" in spec:
        return "security"
    if "shipping" in spec:
        return "greenfield"
    # The poor and casual levels have no SPEC.md (amendment of 2026-11-12): the project's name.
    project = cwd / "pyproject.toml"
    text = project.read_text(encoding="utf-8") if project.is_file() else ""
    if 'name = "alerts"' in text:
        return "security"
    if 'name = "shipping"' in text:
        return "greenfield"
    if 'name = "rides"' in text:
        # The rides fixture (rides/fixture/pyproject.toml); its SPEC.md is docs/SPEC.md or absent.
        return "rides"
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
    "rides": "rides",
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
    elif scenario == "rides":
        # A minimal importable package: the dry run checks the apparatus, not the platform (the
        # code copy of a block has no rides/reference).
        package_dir = cwd / "src" / "rides"
        package_dir.mkdir(parents=True, exist_ok=True)
        init = package_dir / "__init__.py"
        if not init.exists():
            init.write_text('"""Dry run: the rides package."""\n', encoding="utf-8")
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
    text = f"Applied the reference solution of {scenario} and one test per id ({len(names)})."
    if _mode(cwd) == "dispute-acceptance" and "disputedAcceptanceTests" in prompt:
        # The frozen tests of this mode cannot pass: the agent disputes them (#94).
        frozen = sorted((cwd / "tests" / "acceptance").glob("test_acceptance_*.py"))
        report = {
            "assumptions": [],
            "alternativesDiscarded": [],
            "lowConfidenceAreas": [],
            "unrequestedChanges": [],
            "disputedAcceptanceTests": [
                {
                    "test": path.relative_to(cwd).as_posix(),
                    "reason": "Dry run: the test expects the package to be missing.",
                }
                for path in frozen
            ],
        }
        text += "\n\n```json\n" + json.dumps({"harnessSelfReport": report}) + "\n```\n"
    return text


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
    if (
        "Write acceptance tests for the task below" in prompt
        and _mode(cwd) == "malformed-acceptance"
    ):
        marker = cwd.parent / "fake-malformed-done"
        if not marker.exists():
            marker.write_text("1", encoding="utf-8")
            return {"tests": "not a list"}  # malformed once, then a valid answer
    if "Write acceptance tests for the task below" in prompt:
        package = PACKAGE.get(_scenario(cwd) or "", "")
        directory = request.get("directory") or "tests/acceptance"
        criteria = [c.get("criterion_id") for c in task.get("acceptance_criteria") or []] or ["ac"]
        # dispute-acceptance: the first proposal cannot pass, the one for a revision can.
        wrong = _mode(cwd) == "dispute-acceptance" and not request.get("revision")
        check = " is None" if wrong else ""
        content = "import importlib\n" + "".join(
            f"\n\ndef test_{cid}_package_exists() -> None:\n    assert importlib.import_module({package!r}){check}\n"
            for cid in criteria
        )
        name = f"{directory}/test_acceptance_{criteria[0]}.py"  # one file per task: no overwrite
        return {"tests": [{"path": name, "content": content}]}
    if "Find where the task below must be implemented" in prompt:
        return {"locations": [], "questions": []}
    if "Survey the architecture of the existing project" in prompt:
        survey: dict[str, Any] = {
            "style": "layered",
            "summary": "Dry run: one source layer.",
            "layers": [{"name": "source", "paths": ["src/**"], "modules": []}],
            "allow": {"source": []},
        }
        if '"organization" (Markdown' in prompt:
            # The reverse engineering fields of architecture.documentation: project (#97).
            survey |= {
                "organization": "Dry run: the package lives under src/.",
                "components": [{"name": "source", "path": "src", "responsibility": "The code."}],
                "conventions": [],
                "guidance": ["Dry run: follow the existing structure."],
            }
        return survey
    if "A person chose the" in prompt and "architecture for the new project" in prompt:
        # The expert's organization plan under architecture.catalogue: experts (#97).
        return {
            "summary": "Dry run: one package.",
            "structure": [{"path": "src", "purpose": "The package."}],
            "practices": ["Dry run: keep the package small."],
        }
    if "is new. From the task below" in prompt:
        option: dict[str, Any] = {
            "benefits": ["simple"],
            "costs": ["none"],
            "fit": "small library",
            "layers": [{"name": "package", "paths": ["src/**"], "modules": []}],
            "allow": {"package": []},
        }
        if "a 'supports' list" in prompt:
            # Under architecture.catalogue: experts every option names what supports it (#97).
            option["supports"] = ["a small library"]
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
    if not prompt.startswith("Eres el product owner"):
        return None
    if "Pruebas disputadas y las razones del agente:" in prompt:
        return (
            "```yaml\ndecision: REVISE\nreason: |\n"
            "  Corrida en seco: la prueba disputada afirma un valor equivocado.\n```"
        )
    ids = sorted(set(re.findall(r"^- (Q-\d+):", prompt, re.MULTILINE)))
    lines = ["```yaml", "answers:"]
    for qid in ids:
        lines += [
            f"  {qid}: |",
            "    La entrada válida más pequeña devuelve el resultado documentado; una entrada inválida lanza el error documentado.",
        ]
    lines += [
        "addCriteria:",
        "  - |",
        "    Ejecutar python -m pytest -q termina con código 0.",
        "```",
    ]
    return "\n".join(lines)


class _Mcp:
    """A minimal MCP client over stdio: the fake host's connection to ``harness mcp serve``."""

    def __init__(self, config: str) -> None:
        import os
        import subprocess

        path = Path(config)
        servers = json.loads(path.read_text(encoding="utf-8") if path.is_file() else config)[
            "mcpServers"
        ]
        server = servers["harness"]
        env = {**os.environ, **(server.get("env") or {})}
        self.proc = subprocess.Popen(
            [server["command"], *server.get("args", [])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.next_id = 0
        self.request("initialize", {"protocolVersion": "2025-06-18"})

    def request(self, method: str, params: dict[str, Any]) -> Any:
        self.next_id += 1
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
            + "\n"
        )
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline()).get("result")

    def tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments}) or {}
        return result.get("structuredContent") or {}

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=60)


def _host(prompt: str, cwd: Path, config: str) -> str:
    """A host session (embedded mode): create the task and start the run on the first prompt,
    implement and continue whenever the run waits for the session."""
    mcp = _Mcp(config)
    steps = []
    first = cwd.parent / "fake-host-prompt.txt"
    if not first.exists():
        first.write_text(prompt, encoding="utf-8")
    starting = "harness_task_create, pasando exactamente" in prompt
    prompt = prompt + "\n" + first.read_text(encoding="utf-8")  # the task's ids for the tests
    try:
        match = re.search(r"```json\n(.*?)\n```", prompt, re.DOTALL)
        if starting and match:
            task = mcp.tool("harness_task_create", {"task": json.loads(match.group(1))})
            run = mcp.tool("harness_run_start", {"taskId": task.get("taskId")})
            steps.append(
                f"started {run.get('executionId')} ({run.get('status')} {run.get('currentPhase')})"
            )
        status = mcp.tool("harness_status", {"run": "latest"})
        execution = status.get("execution") or {}
        if execution.get("currentPhase") in ("IMPLEMENTATION", "VERIFICATION") and execution.get(
            "status"
        ) in ("BLOCKED", "FAILED"):
            steps.append(_implement(cwd, prompt))
            mcp.tool("harness_check", {"run": execution.get("executionId")})
            after = mcp.tool("harness_run_continue", {"run": execution.get("executionId")})
            steps.append(
                f"continued: {after.get('status')} {after.get('currentPhase')} exit {after.get('exitCode')}"
            )
        else:
            steps.append(
                f"waiting for a person: {execution.get('status')} {execution.get('currentPhase')}"
            )
    finally:
        mcp.close()
    return "; ".join(steps)


def _session(argv: list[str], cwd: Path) -> tuple[str | None, bool]:
    """The session of the call and whether ``--resume`` names one this stand-in kept."""
    sessions = cwd.parent / "fake-sessions"
    resume = _arg(argv, "--resume")
    if resume:
        return resume, (sessions / resume).is_file()
    session = _arg(argv, "--session-id")
    if session:
        sessions.mkdir(exist_ok=True)
        (sessions / session).write_text("1", encoding="utf-8")
    return session, True


def _turn_limit(argv: list[str], cwd: Path) -> int | None:
    """The cap an implement call stops on in the ``turn-limit`` mode (the first such call only)."""
    cap = _arg(argv, "--max-turns")
    marker = cwd.parent / "fake-turn-limit-done"
    if _mode(cwd) != "turn-limit" or not cap or marker.exists():
        return None
    marker.write_text("1", encoding="utf-8")
    return int(cap)


def main(argv: list[str]) -> int:
    prompt = _arg(argv, "-p") or sys.stdin.read()
    if sys.platform == "win32" and prompt.startswith("--"):
        # agentlib gives the prompt on stdin on Windows (the command line is limited to 32767
        # characters), so "-p" is followed by the next option.
        # The text mode of the pipe on Windows turns its newlines into CRLF: read them back as
        # LF, or the ```json block of the request is not found.
        prompt = sys.stdin.buffer.read().decode("utf-8").replace("\r\n", "\n")
    model = _arg(argv, "--model") or "fake"
    cwd = Path.cwd()
    session, known = _session(argv, cwd)
    if not known:
        return _print(
            f"No conversation found with session ID: {session}", model, session, "missing"
        )
    if "--mcp-config" in argv:
        text = _host(prompt, cwd, _arg(argv, "--mcp-config") or "{}")
        return _print(text, model, session)
    text = _product_owner(prompt)
    if text is None and prompt.startswith("Antes de implementar la tarea de abajo puedes"):
        # direct-po's questions turn (direct_po.QUESTIONS_PROMPT): one question.
        text = "```yaml\nquestions:\n  - |\n    ¿Qué pasa con una entrada inválida?\n```"
    if text is None:
        result = _call(prompt, cwd)
        if result is not None:
            text = json.dumps({"status": "PASSED", "summary": "Dry-run answer.", "result": result})
        elif (cap := _turn_limit(argv, cwd)) is not None:
            return _print(f"Reached maximum number of turns ({cap})", model, session, "cap", cap)
        else:
            text = _implement(cwd, prompt)
    return _print(text, model, session)


def _print(
    text: str, model: str, session: str | None = None, error: str = "", turns: int = 1
) -> int:
    """Print the result object; ``error`` is ``cap`` (the turn cap) or ``missing`` (no session)."""
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
                "subtype": "error_max_turns" if error == "cap" else "success",
                "is_error": bool(error),
                "result": text,
                "num_turns": 0 if error == "missing" else turns,
                "duration_api_ms": 0,
                "total_cost_usd": 0.0,
                "session_id": session or str(uuid.uuid4()),
                "usage": usage,
                "modelUsage": {model: {"inputTokens": 0, "outputTokens": 0, "costUSD": 0.0}},
                "permission_denials": [],
                "terminal_reason": "max_turns" if error == "cap" else "completed",
            }
        )
    )
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
