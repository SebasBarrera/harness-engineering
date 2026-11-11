"""Shared pieces of the robustness suites H47, H48 and H50 (no model is called).

* ``governed_robust``: ``resource_support.governed`` with the fixture provider
  ``robust_agent.py`` instead of ``budget_agent.py`` (the same workspace, configuration and grant).
* ``ProcHarness``: the harness started as ``python -m governed_harness`` with the interpreter of
  its virtual environment, through ``Popen``, so another thread can kill exactly the harness
  process that is running (``current``) and stop the thread's later commands (``stop``). On
  Windows ``harness.exe`` is a launcher with a child process; killing it would not be the same.
* Killing: ``kill_process`` (POSIX ``SIGKILL``, Windows ``TerminateProcess``) and ``kill_tree``
  (the "power cut": POSIX ``SIGSTOP`` then ``SIGKILL`` to the process and every descendant,
  Windows ``taskkill /F /T``); ``alive`` checks a pid (POSIX ``kill 0``, Windows ``tasklist``).
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import resource_support  # noqa: E402

WINDOWS = sys.platform == "win32"
ROBUST_AGENT = HERE / "robust_agent.py"
# The threshold discount without float annotations: the correct change of resource_support
# (``CORRECT_CODE``) adds ``subtotal: float``, which the risk factor ``floatMoney`` (``block`` in
# ``harness init``) turns into a HIGH finding, so that run can never be approved and closed.
# Amendment of 2026-11-10 in PREREGISTRO.md (H47, H48, H50).
APPROVABLE_CODE = (
    "def apply_discount(subtotal, threshold, rate):\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)


def approvable_spec() -> dict[str, Any]:
    """``resource_support.correct_spec`` with ``APPROVABLE_CODE`` (same tests)."""
    spec = resource_support.correct_spec()
    spec["change"] = {"src/sample/pricing.py": APPROVABLE_CODE}
    return spec


def governed_robust(
    run_dir: Path, args: Any, spec: dict[str, Any], **kwargs: Any
) -> resource_support.Workspace:
    """``resource_support.governed`` with ``robust_agent.py`` as the provider's script."""
    previous = resource_support.AGENT
    resource_support.AGENT = ROBUST_AGENT
    try:
        return resource_support.governed(run_dir, args, spec, **kwargs)
    finally:
        resource_support.AGENT = previous


def expected_files(task: dict[str, Any]) -> dict[str, str]:
    """What ``approvable_spec`` leaves: the discount and one test per requirement and
    criterion id of the task (``budget_agent.write_change``, ``traceTests``)."""
    names = [r["requirementId"] for r in task["requirements"]] + [
        c["criterionId"] for c in task["acceptanceCriteria"]
    ]
    tests = [resource_support.TRACE_HEADER] + [
        f"\n\ndef test_{name}() -> None:\n    assert {resource_support.TRACE_ASSERT}\n"
        for name in dict.fromkeys(names)
    ]
    return {"src/sample/pricing.py": APPROVABLE_CODE, "tests/test_pricing.py": "".join(tests)}


def harness_python(executable: str) -> list[str]:
    """``[python, -m, governed_harness]`` for the interpreter the harness executable runs on;
    the executable itself when that interpreter cannot be found."""
    path = Path(executable)
    if not path.is_absolute():
        found = __import__("shutil").which(executable)
        path = Path(found) if found else path
    if WINDOWS:
        candidate = path.parent / "python.exe"
        if candidate.exists():
            return [str(candidate), "-m", "governed_harness"]
        return [executable]
    try:
        first = path.read_bytes()[:256].split(b"\n", 1)[0].decode("utf-8", "replace")
    except OSError:
        return [executable]
    if first.startswith("#!") and "python" in first:
        interpreter = first[2:].strip().split()[0]
        if Path(interpreter).exists():
            return [interpreter, "-m", "governed_harness"]
    return [executable]


class ProcHarness(resource_support.Utf8Harness):
    """``Utf8Harness`` through ``Popen``: ``current`` is the running harness process."""

    def __init__(self, executable: str, env: dict[str, str]) -> None:
        super().__init__(executable, env)
        self.command = harness_python(executable)
        self.current: subprocess.Popen[str] | None = None
        self.current_label = ""
        self.stopped = threading.Event()
        self.lock = threading.Lock()

    def run(
        self, cwd: Path, *args: str, label: str = "", timeout: float | None = None
    ) -> tuple[int, Any, float]:
        started = time.monotonic()
        with self.lock:
            if self.stopped.is_set():
                self.steps.append({"step": label or " ".join(args[:2]), "skipped": "stopped"})
                return -999, {"stdout": "", "stderr": "stopped"}, 0.0
            process = subprocess.Popen(
                [*self.command, *args],
                cwd=cwd,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                encoding="utf-8",
                errors="replace",
            )
            self.current = process
            self.current_label = label or " ".join(args[:2])
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            code = process.returncode
        except subprocess.TimeoutExpired:
            kill_tree(process.pid)
            stdout, stderr = process.communicate()
            code, stderr = -9, "TIMEOUT"
        finally:
            with self.lock:
                self.current = None
        seconds = time.monotonic() - started
        payload: Any = {"stdout": (stdout or "")[-800:], "stderr": (stderr or "")[-800:]}
        for stream in (stdout, stderr):
            try:
                payload = json.loads(stream)
                break
            except (json.JSONDecodeError, TypeError):
                continue
        self.steps.append(
            {"step": label or " ".join(args[:2]), "exitCode": code, "seconds": round(seconds, 3)}
        )
        return code, payload, seconds


# ----- processes -------------------------------------------------------------------------------
def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if WINDOWS:
        listed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            check=False,
        )
        return f'"{pid}"' in listed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill 0: it is not running.
    with contextlib.suppress(OSError):
        state = Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
        return state != "Z"
    return True


def command_line(pid: int) -> str:
    """The command line of a process ("" when it cannot be read)."""
    if WINDOWS:
        shown = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f'(Get-CimInstance Win32_Process -Filter "ProcessId={int(pid)}").CommandLine',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return shown.stdout.strip()
    with contextlib.suppress(OSError):
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    shown = subprocess.run(
        ["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return shown.stdout.strip()


def is_own_fixture(pid: int, run_dir: Path) -> bool:
    """Whether ``pid`` is the fixture of this case: ``robust_agent.py`` with a specification
    under ``run_dir``. A pid read from a marker can belong to another process once the fixture
    exited (pids are reused), and is never killed then."""
    line = command_line(pid).replace("\\", "/").lower()
    return "robust_agent.py" in line and run_dir.as_posix().lower() in line


def descendants(pid: int) -> list[int]:
    """Every descendant of ``pid`` (POSIX, ``pgrep -P`` recursively)."""
    found: list[int] = []
    pending = [pid]
    while pending:
        parent = pending.pop()
        listed = subprocess.run(
            ["pgrep", "-P", str(parent)], capture_output=True, text=True, check=False
        )
        children = [int(item) for item in listed.stdout.split() if item.strip().isdigit()]
        found.extend(children)
        pending.extend(children)
    return found


def kill_process(pid: int) -> None:
    """Kill one process, no handler runs: POSIX ``SIGKILL``, Windows ``TerminateProcess``."""
    if WINDOWS:
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            kernel.TerminateProcess(handle, 1)
            kernel.CloseHandle(handle)
        return
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def kill_tree(pid: int) -> list[int]:
    """Kill a process and all its descendants at once; the pids killed."""
    if WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
        return [pid]
    tree = [pid, *descendants(pid)]
    for item in tree:
        with contextlib.suppress(ProcessLookupError):
            os.kill(item, signal.SIGSTOP)
    # Children started after the freeze (none should be, the tree is stopped) are looked for once.
    tree.extend(p for p in descendants(pid) if p not in tree)
    for item in tree:
        with contextlib.suppress(ProcessLookupError):
            os.kill(item, signal.SIGKILL)
    return tree


# ----- the event store -------------------------------------------------------------------------
def all_events(database: Path) -> list[dict[str, Any]]:
    """Every event of the database, oldest first, with its phase execution id."""
    if not database.exists():
        return []
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    try:
        rows = list(
            connection.execute(
                "SELECT execution_id, execution_sequence, event_type, phase_execution_id, "
                "payload_json FROM events ORDER BY rowid"
            )
        )
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    out = []
    for run_id, sequence, kind, phase_id, payload in rows:
        try:
            data = json.loads(payload)
        except ValueError:
            data = {}
        out.append(
            {
                "runId": run_id,
                "sequence": sequence,
                "type": kind,
                "phaseExecutionId": phase_id,
                "payload": data,
            }
        )
    return out


def open_phase(events: list[dict[str, Any]]) -> str | None:
    """The last phase started and not completed (where a kill landed)."""
    started: dict[str, str] = {}
    for event in events:
        key = str(event.get("phaseExecutionId"))
        if event["type"] == "phase.started":
            started[key] = str(event["payload"].get("phaseId"))
        elif event["type"] == "phase.completed":
            started.pop(key, None)
    return list(started.values())[-1] if started else None


def git_porcelain(ws: Path) -> list[str]:
    from common import git

    return [
        line
        for line in git(ws, "status", "--porcelain", "--untracked-files=all").splitlines()
        if line.strip() and ".harness" not in line
    ]


__all__ = [
    "APPROVABLE_CODE",
    "approvable_spec",
    "ProcHarness",
    "WINDOWS",
    "alive",
    "all_events",
    "descendants",
    "expected_files",
    "git_porcelain",
    "governed_robust",
    "harness_python",
    "is_own_fixture",
    "command_line",
    "kill_process",
    "kill_tree",
    "open_phase",
]
