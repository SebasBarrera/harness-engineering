#!/usr/bin/env python3
"""H51: ``harness doctor`` and an incomplete environment, ``CATALOGO.md`` §2.9 and
``PREREGISTRO.md`` (third freeze). No model is called.

A healthy control (C00: the sample project after ``harness init`` with the fixture provider, a
repository-local Git identity, a clean tree) and ten environments with one defect each over it:
E01 no ``git`` on ``PATH``; E02 no pytest (``python`` on ``PATH`` is a virtual environment
without it); E03 a dirty tree; E04 no sandbox mechanism under ``runtime.agentSandbox: enforce``
(Linux without ``bwrap`` on ``PATH``; Windows as it is; not evaluated on macOS); E05 an old
Python (a virtual environment of an interpreter older than 3.12 found on the host; not evaluated
when there is none); E06 ``.harness/`` not ignored; E07 the agent CLI absent; E08 a repository
without commits; E09 an invalid ``project.yaml``; E10 no Git identity.

``harness doctor --path . --json`` runs in each; a defect is *detected* when a check it
corresponds to (``EXPECTED``) is ``WARNING`` or ``FAILED`` and differs from the control. On POSIX
a program is taken off ``PATH`` by shadowing each ``PATH`` directory with a directory of links
to everything else; on Windows the ``PATH`` entries that hold it are dropped.

Usage:
  python evaluation/deterministic/h51_doctor.py --out DIR [--work DIR] [--project-venv DIR]
      [--harness PATH] [--wheel WHEEL] [--only C00,E01,...] [--reps 1] [--summarize-only]

Writes ``DIR/h51-doctor.jsonl``, ``DIR/h51-doctor-summary.json``, ``DIR/environment.json`` and
``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import git  # noqa: E402
from resource_support import (  # noqa: E402
    cleanup,
    main,
    proportion,
    saved_config,
    stamp_dir,
)
from robustness_support import approvable_spec, governed_robust  # noqa: E402
from suite_support import WINDOWS, bin_dir, rmtree  # noqa: E402

CASES: list[dict[str, Any]] = [
    {"id": "C00", "defect": "none (control)"},
    {"id": "E01", "defect": "git is not on PATH"},
    {"id": "E02", "defect": "pytest is not installed for the python on PATH"},
    {"id": "E03", "defect": "the working tree is dirty"},
    {"id": "E04", "defect": "no sandbox mechanism with runtime.agentSandbox: enforce"},
    {"id": "E05", "defect": "the python on PATH is older than 3.12"},
    {"id": "E06", "defect": ".harness/ is not ignored by Git"},
    {"id": "E07", "defect": "the agent CLI of the provider is absent"},
    {"id": "E08", "defect": "the repository has no commit"},
    {"id": "E09", "defect": "project.yaml is invalid"},
    {"id": "E10", "defect": "Git has no identity"},
]
EXPECTED = {
    "E01": ["git"],
    "E02": ["validators"],
    "E03": ["environment"],
    "E04": ["agentSandbox"],
    "E05": ["python"],
    "E06": ["harnessIgnored"],
    "E07": ["agentProvider"],
    "E08": ["baseline"],
    "E09": ["configuration"],
    "E10": ["gitIdentity"],
}
FLAGGED = {"WARNING", "FAILED"}
CONTROL: dict[str, Any] = {}


def conditions_of(case: dict[str, Any]) -> list[str]:
    del case
    return ["H"]


# ----- the environment -------------------------------------------------------------------------
def without_programs(env: dict[str, str], names: set[str], shadow_root: Path) -> dict[str, str]:
    """``env`` with the programs ``names`` taken off ``PATH``."""
    entries = [p for p in env["PATH"].split(os.pathsep) if p]
    kept: list[str] = []
    for index, entry in enumerate(entries):
        directory = Path(entry)
        holds = [
            n
            for n in names
            if (directory / n).exists() or (WINDOWS and (directory / f"{n}.exe").exists())
        ]
        if not holds:
            kept.append(entry)
            continue
        if WINDOWS:
            continue  # dropped: symbolic links need a privilege on Windows
        shadow = shadow_root / f"path-{index}"
        shadow.mkdir(parents=True, exist_ok=True)
        for item in directory.iterdir():
            if item.name not in names and not (shadow / item.name).exists():
                os.symlink(item, shadow / item.name)
        kept.append(str(shadow))
    return {**env, "PATH": os.pathsep.join(kept)}


def make_venv(interpreter: list[str], target: Path) -> Path | None:
    """A virtual environment without pip of ``interpreter``; its bin directory."""
    if not (bin_dir(target)).exists():
        completed = subprocess.run(
            [*interpreter, "-m", "venv", "--without-pip", str(target)],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        if completed.returncode != 0:
            return None
    return bin_dir(target)


def old_interpreter() -> tuple[list[str], str] | None:
    """An interpreter older than 3.12 on this host, and its version."""
    candidates: list[list[str]] = []
    if WINDOWS and shutil.which("py"):
        listed = subprocess.run(["py", "-0p"], capture_output=True, text=True, check=False)
        for line in listed.stdout.splitlines():
            match = re.search(r"-V:(3\.(\d+))\S*\s+\*?\s*(.+python\.exe)", line, re.IGNORECASE)
            if match and int(match.group(2)) < 12:
                candidates.append([match.group(3).strip()])
    for minor in range(11, 6, -1):
        found = shutil.which(f"python3.{minor}")
        if found:
            candidates.append([found])
    for command in candidates:
        probe = subprocess.run(
            [*command, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True,
            text=True,
            check=False,
        )
        version = probe.stdout.strip()
        if probe.returncode == 0 and version and tuple(map(int, version.split(".")[:2])) < (3, 12):
            return command, version
    return None


def doctor(h: Any, ws: Path) -> tuple[int, dict[str, Any]]:
    code, payload, _ = h.run(ws, "doctor", "--path", ".", "--json", label="doctor")
    return code, payload if isinstance(payload, dict) else {}


def edit_config(ws: Path, change: Any) -> None:
    path = ws / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(config)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def identity(ws: Path) -> None:
    git(ws, "config", "user.name", "Eval Person")
    git(ws, "config", "user.email", "eval.person@example.invalid")


# ----- one case --------------------------------------------------------------------------------
def runner(
    case: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    del condition, rep
    run_dir = stamp_dir(args, "h51", case["id"])
    workspace = governed_robust(run_dir, args, approvable_spec())
    saved_config(args, "H", workspace)
    ws, h = workspace.ws, workspace.h
    if case["id"] != "E10":
        identity(ws)
    env = dict(h.env)
    record: dict[str, Any] = {"defect": case["defect"], "deviations": list(workspace.deviations)}
    not_evaluated = None
    cid = case["id"]
    if cid == "E01":
        env = without_programs(env, {"git"}, run_dir / "shadow")
    elif cid == "E02":
        bin_path = make_venv([sys.executable], args.work / "h51-venv-nopytest")
        if bin_path is None:
            not_evaluated = "could not create a virtual environment without pytest"
        else:
            env["PATH"] = os.pathsep.join([str(bin_path), env["PATH"]])
    elif cid == "E03":
        target = ws / "src" / "sample" / "pricing.py"
        target.write_text(target.read_text(encoding="utf-8") + "# local edit\n", encoding="utf-8")
    elif cid == "E04":
        if sys.platform == "darwin":
            not_evaluated = "macOS always has sandbox-exec (declared in PREREGISTRO.md)"
        edit_config(ws, lambda c: c.setdefault("runtime", {}).update(agentSandbox="enforce"))
        if sys.platform.startswith("linux"):
            env = without_programs(env, {"bwrap"}, run_dir / "shadow")
        record["deviations"] = [d for d in record["deviations"] if "agentSandbox" not in d]
    elif cid == "E05":
        found = old_interpreter()
        if found is None:
            not_evaluated = "no Python interpreter older than 3.12 was found on this host"
        else:
            command, version = found
            record["oldPython"] = version
            bin_path = make_venv(command, args.work / f"h51-venv-py{version}")
            if bin_path is None:
                not_evaluated = f"could not create a virtual environment of Python {version}"
            else:
                env["PATH"] = os.pathsep.join([str(bin_path), env["PATH"]])
    elif cid == "E06":
        ignore = ws / ".gitignore"
        ignore.write_text(
            "".join(
                line + "\n"
                for line in ignore.read_text(encoding="utf-8").splitlines()
                if ".harness" not in line
            ),
            encoding="utf-8",
        )
        exclude = ws / ".git" / "info" / "exclude"
        if exclude.exists():
            exclude.write_text(
                "".join(
                    line + "\n"
                    for line in exclude.read_text(encoding="utf-8").splitlines()
                    if ".harness" not in line
                ),
                encoding="utf-8",
            )
        git(ws, "add", ".gitignore")
        git(ws, "commit", "-qm", "stop ignoring .harness")
    elif cid == "E07":
        edit_config(
            ws,
            lambda c: c["agentProviders"]["fixture"].update(
                command=["h51-missing-agent-cli", *c["agentProviders"]["fixture"]["command"][1:]]
            ),
        )
    elif cid == "E08":
        rmtree(ws / ".git")
        git(ws, "init", "-q")
        identity(ws)
    elif cid == "E09":
        edit_config(ws, lambda c: c.setdefault("runtime", {}).update(h51UnknownKey=True))
    h.env = env
    code, report = doctor(h, ws)
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    statuses = {name: (item or {}).get("status") for name, item in checks.items()}
    record.update(
        {
            "doctorExitCode": code,
            "doctorStatus": report.get("status"),
            "checkStatuses": statuses,
            # A check's fix: its own hint, or the hint of one of its entries (the validators
            # check carries one per validator).
            "hints": {
                name: bool((item or {}).get("hint"))
                or any(
                    isinstance(entry, dict) and entry.get("hint")
                    for entry in (item or {}).get("validators") or ()
                )
                for name, item in checks.items()
            },
        }
    )
    if cid == "C00":
        CONTROL.clear()
        CONTROL.update(statuses)
        record["controlFlagged"] = sorted(n for n, s in statuses.items() if s in FLAGGED)
    else:
        control = record_control(args)
        changed = sorted(
            n
            for n, s in statuses.items()
            if s != control.get(n) and (s in FLAGGED or control.get(n) in FLAGGED)
        )
        hits = [
            n
            for n in EXPECTED[cid]
            if statuses.get(n) in FLAGGED and statuses.get(n) != control.get(n)
        ]
        if cid == "E05" and hits:
            # The python check reports the harness's own interpreter: it counts only when it
            # names the old version.
            version = str(record.get("oldPython") or "")
            hits = [n for n in hits if version and version in str(checks.get(n))]
        mentioned = cid == "E03" and bool(
            ((checks.get("environment") or {}).get("dirtyTree") or {}).get("paths")
        )
        any_failed = any(s == "FAILED" for s in statuses.values())
        record.update(
            {
                "expectedChecks": EXPECTED[cid],
                "changedChecks": changed,
                "detected": None if not_evaluated else bool(hits),
                "detectedBy": hits,
                "withFix": None
                if not_evaluated
                else bool(hits) and all(record["hints"].get(n) for n in hits),
                "mentionedWithoutFlag": mentioned and not hits,
                "exit2WhenAppropriate": (code == 2) == any_failed,
                "messages": {
                    n: str((checks.get(n) or {}).get("message") or "")[:300]
                    for n in set(changed) | set(EXPECTED[cid])
                },
            }
        )
        if not_evaluated:
            record["notEvaluatedBecause"] = not_evaluated
    record["brief"] = (
        f"exit {code} detected={record.get('detected')}" if cid != "C00" else f"exit {code}"
    )
    cleanup(run_dir, args.keep_work)
    return record


def record_control(args: argparse.Namespace) -> dict[str, Any]:
    """The control's check statuses: from this invocation, else from a control run now (when
    ``--only`` leaves C00 out)."""
    if not CONTROL:
        runner(CASES[0], "H", args, 1)
    return CONTROL


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    defects = [r for r in ok if r["case"] != "C00"]
    evaluated = [r for r in defects if r.get("detected") is not None]
    detected = sum(1 for r in evaluated if r["detected"])
    control = next((r for r in ok if r["case"] == "C00"), {})
    summary = {
        "suite": "H51",
        "title": "harness doctor and an incomplete environment",
        "platform": sys.platform,
        "control": {
            "doctorExitCode": control.get("doctorExitCode"),
            "flagged": control.get("controlFlagged"),
        },
        "byCase": {
            r["case"]: {
                k: r.get(k)
                for k in (
                    "defect",
                    "detected",
                    "detectedBy",
                    "withFix",
                    "mentionedWithoutFlag",
                    "doctorExitCode",
                    "exit2WhenAppropriate",
                    "changedChecks",
                    "notEvaluatedBecause",
                )
            }
            for r in defects
        },
        "detected": proportion(detected, len(evaluated)),
        "withFix": proportion(sum(1 for r in evaluated if r.get("withFix")), len(evaluated)),
        "exit2WhenAppropriate": proportion(
            sum(1 for r in defects if r.get("exit2WhenAppropriate")), len(defects)
        ),
        "notEvaluated": sorted(r["case"] for r in defects if r.get("detected") is None),
    }
    summary["criteria"] = {
        "detected10of10": {
            "value": f"{detected}/10 detected, {len(evaluated)} evaluated",
            "met": detected == 10 and len(evaluated) == 10,
        },
        "note": "Pre-registered in PREREGISTRO.md (H51); evaluated as is. A case not evaluated "
        "counts against the criterion.",
    }
    return summary


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H51",
            title="harness doctor and an incomplete environment",
            stem="h51-doctor",
            cases=CASES,
            conditions_of=conditions_of,
            all_conditions=["H"],
            runner=runner,
            summarize=summarize,
        )
    )
