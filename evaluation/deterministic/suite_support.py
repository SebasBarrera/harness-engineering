"""Shared pieces of the deterministic suites of the 2.0.0 campaign (H14, H18, H22, H23): no model
is ever called.

* ``portable_env``: the environment of ``common.isolated_env`` on Linux and macOS (the same keys
  and values), and on Windows the variables a Windows process needs (``SYSTEMROOT``, ``TEMP``,
  ...). ``PYTHONPATH`` is kept when it is set, so a source checkout can be smoke-tested with the
  harness of a virtual environment; the campaign (``run_deterministic.py``) does not set it.
* ``FIXTURE_AGENT``: a command provider (provider protocol 1.1) driven by a JSON specification:
  ``implement`` writes the files of the specification (and, when asked, a test per requirement
  and criterion id the request names); every read-only kind answers an empty, valid result;
  one call kind can be given a raw answer instead (H23). Every request is logged with its size
  measured as the harness measures it (``agents/contract.py:request_bytes``: the JSON with sorted
  keys, UTF-8) and its estimated tokens (characters / 4, as ``fault_provider.py:usage``).
* ``configure``: the configuration of a condition (``harness init`` as written, the 1.0.0
  ``harness-core`` file of ``conditions.py``, or either with a mutation) pointed at the fixture,
  with the provider command granted as ``conditions.grant_provider_command`` does.

On Windows there is no agent sandbox (``runtime/sandbox.py``): the suites turn
``runtime.agentSandbox`` off there and record it as a deviation in every record (``deviations``).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import conditions  # noqa: E402
from common import GIT_ENV, Harness, git, init_repo, simulated_person  # noqa: E402

WINDOWS = sys.platform == "win32"


def import_agentlib() -> Any:
    """``evaluation/agentlib.py``, which imports ``pwd`` (absent on Windows): there a stub stands
    in for that import only (``build_prompt`` does not use it)."""
    if WINDOWS and "pwd" not in sys.modules:
        import types

        sys.modules["pwd"] = types.ModuleType("pwd")
    import agentlib

    return agentlib


def direct_prompt(task: dict[str, Any], feedback: str | None = None) -> str:
    """The direct agent's prompt of the evaluation: ``agentlib.build_prompt``."""
    return str(import_agentlib().build_prompt(task, feedback))


def bin_dir(venv: Path) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin")


def portable_env(path_dirs: list[Path | str], state_root: Path) -> dict[str, str]:
    """``common.isolated_env`` on POSIX; on Windows the same keys plus what a process needs."""
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "anchors").mkdir(exist_ok=True)
    (state_root / "state").mkdir(exist_ok=True)
    if WINDOWS:
        system = os.environ.get("SYSTEMROOT", r"C:\Windows")
        base = [str(p) for p in path_dirs] + [
            str(Path(system) / "System32"),
            system,
            *(p for p in os.environ.get("PATH", "").split(os.pathsep) if "Git" in p),
        ]
        env = {
            "PATH": os.pathsep.join(base),
            "HOME": str(Path.home()),
            "USERPROFILE": os.environ.get("USERPROFILE", str(Path.home())),
            "SYSTEMROOT": system,
            "TEMP": os.environ.get("TEMP", ""),
            "TMP": os.environ.get("TMP", ""),
            "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
            "COMSPEC": os.environ.get("COMSPEC", ""),
            "LANG": "en_US.UTF-8",
            "PYTHONUTF8": "1",
        }
    else:
        env = {
            "PATH": ":".join(
                [*(str(p) for p in path_dirs), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
            ),
            "HOME": os.environ["HOME"],
            "LANG": "en_US.UTF-8",
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        }
    env.update(
        {
            "HARNESS_ANCHOR_DIR": str(state_root / "anchors"),
            "HARNESS_STATE_DIR": str(state_root / "state"),
            **GIT_ENV,
        }
    )
    if os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    return env


def rmtree(path: Path) -> None:
    """Remove a tree, also the read-only objects Git leaves on Windows."""

    def retry(function: Callable[..., Any], target: str, _error: BaseException) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if path.exists():
        shutil.rmtree(path, onexc=retry)


# ----- the sample project ----------------------------------------------------------------------
SAMPLE_CODE = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal\n"
)
SAMPLE_TEST = (
    "from sample import apply_discount\n\n\n"
    "def test_below_threshold() -> None:\n"
    "    assert apply_discount(99, 100, 0.1) == 99\n"
)


def sample_project(root: Path) -> None:
    """The sample project of ``friction.py`` (a pricing function and one test), committed."""
    (root / "src" / "sample").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "sample" / "__init__.py").write_text("from .pricing import apply_discount\n")
    (root / "src" / "sample" / "pricing.py").write_text(SAMPLE_CODE)
    (root / "tests" / "test_pricing.py").write_text(SAMPLE_TEST)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n\n'
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
    )
    (root / "README.md").write_text("# Sample\n")
    (root / ".gitignore").write_text(".harness/\n__pycache__/\n.pytest_cache/\n")
    init_repo(root)


def sample_task(task_id: str, *, subtotal: int = 100, expected: int = 90) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "title": "Threshold discount",
        "intent": "Apply the discount only when the subtotal meets the threshold.",
        "requirements": [
            {
                "requirementId": "req_threshold",
                "text": f"apply_discount({subtotal}, 100, 0.1) returns {expected}.",
            }
        ],
        "acceptanceCriteria": [
            {
                "criterionId": "ac_threshold",
                "text": f"apply_discount({subtotal}, 100, 0.1) returns {expected}.",
            }
        ],
        "metadata": {"ownedPaths": ["src/sample/pricing.py", "tests/test_pricing.py"]},
    }


def sample_change(*, correct: bool = True) -> dict[str, str]:
    """The change the fixture writes: the reference discount and a test naming the ids; with
    ``correct=False`` the code keeps the old behaviour, so the new test fails every time."""
    body = "subtotal * (1 - rate) if subtotal >= threshold else subtotal" if correct else "subtotal"
    return {
        "src/sample/pricing.py": (
            "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
            f"    return {body}\n"
        ),
        "tests/test_pricing.py": SAMPLE_TEST
        + "\n\ndef test_req_threshold_ac_threshold() -> None:\n"
        + "    assert apply_discount(100, 100, 0.1) == 90\n",
    }


# ----- the fixture provider --------------------------------------------------------------------
FIXTURE_AGENT = r'''"""Fixture command provider of the campaign suites (no model). See suite_support.py."""
import json, math, sys, time
from pathlib import Path

started = time.monotonic()
spec = json.loads(Path(sys.argv[sys.argv.index("--spec") + 1]).read_text(encoding="utf-8"))
raw = sys.stdin.buffer.read().decode("utf-8")
request = json.loads(raw)
kind = request.get("kind", "implement")
panel = kind == "review" and "outputContract" in request
log = Path(spec["log"])
earlier = []
if log.exists():
    earlier = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
number = 1 + sum(1 for c in earlier if c["kind"] == kind and c.get("panel") == panel)
task = request.get("task") if isinstance(request.get("task"), dict) else {}


def ids(items, *names):
    found = []
    for item in items or []:
        if isinstance(item, dict):
            for name in names:
                if item.get(name):
                    found.append(str(item[name]))
                    break
    return found


results = {
    "clarify": {"questions": []},
    "review": {"findings": []},
    "acceptance": {"tests": []},
    "locate": {"locations": [], "questions": []},
}
if kind == "plan":
    results["plan"] = {"subtasks": [{
        "title": str(task.get("title") or "The whole task"),
        "requirements": ids(task.get("requirements"), "requirementId", "requirement_id"),
        "criteria": ids(task.get("acceptanceCriteria") or task.get("acceptance_criteria"),
                        "criterionId", "criterion_id"),
        "constraints": [],
    }]}
if kind == "architecture":
    results["architecture"] = (
        {"options": []} if request.get("mode") == "advise"
        else {"style": "custom", "summary": "No layering.", "layers": [], "allow": {},
              # architecture.documentation: project (reverse engineering), as
              # scripts/measure_friction.py answers it.
              "organization": "None.",
              "components": [{"name": "app", "path": ".", "responsibility": "The project."}],
              "conventions": [], "guidance": ["Follow the structure."]}
    )
inject = spec.get("inject") or {}
injected = (
    inject.get("kind") == kind
    and bool(inject.get("panel")) == panel
    and (inject.get("repeat") == "always" or number == 1)
)
if injected:
    out = inject["stdout"]
elif kind == "implement":
    for path, content in (spec.get("change") or {}).items():
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    trace = spec.get("traceTests")
    if trace:
        names = ids(task.get("requirements"), "requirementId", "requirement_id") + ids(
            task.get("acceptanceCriteria") or task.get("acceptance_criteria"),
            "criterionId", "criterion_id")
        lines = [trace["header"]]
        for name in names:
            lines.append(f"\n\ndef test_{name}() -> None:\n    assert {trace['assert']}\n")
        Path(trace["path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(trace["path"]).write_text("".join(lines), encoding="utf-8")
    answer = {"status": "PASSED", "summary": "Change written"}
    answer["usage"] = {"inputTokens": math.ceil(len(raw) / 4), "outputTokens": 10}
    out = json.dumps(answer)
else:
    if panel:
        result = {"verdict": "PASS", "findings": [], "summary": "No finding."}
    else:
        result = results.get(kind, {})
    answer = {"status": "PASSED", "summary": kind, "result": result}
    answer["usage"] = {"inputTokens": math.ceil(len(raw) / 4),
                       "outputTokens": math.ceil(len(json.dumps(answer)) / 4)}
    out = json.dumps(answer)
contract = request.get("agentContract") if isinstance(request.get("agentContract"), dict) else {}
record = {
    "kind": kind,
    "panel": panel,
    "number": number,
    "injected": injected,
    "requestBytes": len(json.dumps(request, sort_keys=True).encode("utf-8")),
    "requestChars": len(raw),
    "estimatedInputTokens": math.ceil(len(raw) / 4),
    "answerBytes": len(out.encode("utf-8")),
    "estimatedOutputTokens": math.ceil(len(out) / 4),
    "topLevelKeys": sorted(request),
    "taskFields": sorted(task),
    "contract": bool(contract),
    "contractIteration": (contract.get("iteration") or {}).get("number"),
    "wallSeconds": round(time.monotonic() - started, 4),
}
try:
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
except OSError:
    pass
sys.stdout.write(out + "\n")
'''


def write_fixture(run_dir: Path, spec: dict[str, Any]) -> list[str]:
    """Write the fixture and its specification; return the provider command."""
    agent = run_dir / "fixture_agent.py"
    agent.write_text(FIXTURE_AGENT, encoding="utf-8")
    spec_path = run_dir / "fixture-spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return ["python", str(agent), "--spec", str(spec_path)]


def find_blob(root: Path, uri: str) -> Path | None:
    """The blob of an ``artifact://sha256/<hex>`` reference under ``root``
    (``evidence/artifact_store.py``: ``blobs/sha256/<2 hex>/<62 hex>``)."""
    digest = uri.removeprefix("artifact://sha256/")
    if len(digest) != 64:
        return None
    return next(iter(root.rglob(f"blobs/sha256/{digest[:2]}/{digest[2:]}")), None)


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ----- configuration ---------------------------------------------------------------------------
def configure(
    ws: Path,
    command: list[str],
    *,
    core: bool = False,
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Write the condition's ``project.yaml`` (after ``harness init``); return it and the
    deviations from the file of the condition (only the agent sandbox on Windows)."""
    path = ws / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if core:
        config = conditions.core_config(config["projectId"])
    config["agentProvider"] = "fixture"
    config["agentProviders"] = {
        "fixture": {"kind": "command", "command": command, "model": "deterministic-fixture"}
    }
    conditions.grant_provider_command(config, command, core=core)
    deviations: list[str] = []
    runtime = config.setdefault("runtime", {})
    if WINDOWS and runtime.get("agentSandbox") == "enforce":
        runtime["agentSandbox"] = "off"
        deviations.append("runtime.agentSandbox off: no agent sandbox exists on Windows")
    if mutate is not None:
        mutate(config)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return config, deviations


def commit_init(ws: Path) -> None:
    if git(ws, "status", "--porcelain").strip():
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "harness init")


# ----- runs ------------------------------------------------------------------------------------
def start_run(
    h: Harness, ws: Path, run_dir: Path, task: dict[str, Any], *, timeout: float = 1800
) -> dict[str, Any]:
    """``task create`` and ``run start``, then the declared simulated person answers the waits
    before DECISION (``common.simulated_person``). Returns the run id, the exit code of the last
    run command and the waits answered."""
    task_path = run_dir / "task.yaml"
    task_path.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    _, created, _ = h.run(
        ws, "task", "create", "--path", ".", "--file", str(task_path), label="task create"
    )
    task_id = created.get("taskId", task["taskId"]) if isinstance(created, dict) else task["taskId"]
    code, started, seconds = h.run(
        ws, "run", "start", "--path", ".", "--task", task_id, label="run start", timeout=timeout
    )
    run_id = started.get("executionId", "") if isinstance(started, dict) else ""
    if not run_id:
        _, latest, _ = h.run(ws, "status", "--path", ".", label="status latest")
        run_id = (
            (latest.get("execution") or {}).get("executionId", "")
            if isinstance(latest, dict)
            else ""
        )
    waits: list[str] = []
    code = simulated_person(h, ws, run_id, task_id, run_dir, waits, code)
    return {"runId": run_id, "taskId": task_id, "runExitCode": code, "waits": waits}


def status_of(h: Harness, ws: Path, run_id: str) -> dict[str, Any]:
    _, status, _ = h.run(ws, "status", "--path", ".", "--run", run_id, label="status")
    return status if isinstance(status, dict) else {}


def approve(h: Harness, ws: Path, run_id: str, actor: str = "human.reviewer") -> int | None:
    """APPROVE at DECISION with the evaluated digest; ``None`` when the run is not there."""
    status = status_of(h, ws, run_id)
    execution = status.get("execution") or {}
    if execution.get("currentPhase") != "DECISION" or execution.get("status") in {"PASSED"}:
        return None
    code, _, _ = h.run(
        ws,
        "gate",
        "decide",
        "--path",
        ".",
        "--run",
        run_id,
        "--decision",
        "APPROVE",
        "--change-set-digest",
        str(execution.get("changeSetDigest")),
        "--actor",
        actor,
        "--rationale",
        "approve",
        label="gate decide APPROVE",
    )
    return code


def events(database: Path, run_id: str) -> list[dict[str, Any]]:
    if not database.exists():
        return []
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return [
            {
                "sequence": row["execution_sequence"],
                "type": row["event_type"],
                "occurredAt": row["occurred_at"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in connection.execute(
                "SELECT * FROM events WHERE execution_id=? ORDER BY execution_sequence",
                (run_id,),
            )
        ]
    except (sqlite3.Error, ValueError):
        return []
    finally:
        connection.close()


def findings(h: Harness, ws: Path, run_id: str) -> list[str]:
    _, listed, _ = h.run(ws, "findings", "list", "--path", ".", "--run", run_id, label="findings")
    if not isinstance(listed, list):
        return []
    return sorted({f"{f.get('severity')}:{f.get('ruleId')}" for f in listed})
