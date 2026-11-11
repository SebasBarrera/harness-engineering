#!/usr/bin/env python3
"""Fixture command provider of the practice and architecture suites H27, H28, H31 and H32. No
model is called.

Provider protocol 1.1, the protocol of ``budget_agent.py``: the request arrives as JSON on
stdin, the answer goes to stdout. What it answers is fixed by a JSON specification:

* ``architecture``, by the request's ``mode``:
  - ``advise``: one option of the catalogue, recommended, with a reason and the characteristics
    that support it (``spec["adviseStyle"]``, default ``layered``), and no layers of its own;
  - ``organize``: a short plan of the expert (summary, structure, practices);
  - ``survey`` (any other mode): no layers, with the reverse engineering fields
    (``architecture.documentation: project``), as ``interception_agent.py`` answers it;
* ``acceptance``: the files of ``spec["acceptance"][format]`` (``pytest`` or ``gherkin``, the
  ``format`` of the request; ``{directory}`` in a path is replaced by the request's
  ``directory``), or no file;
* ``implement``: writes ``spec["write"]`` (whole files) and deletes ``spec["delete"]``. A write
  or deletion the operating system refuses (the write sandbox) is not retried: it is logged as
  denied and the answer still reports success, as an agent that ignores the error would;
* every other read-only kind: an empty, valid result.

Each call is appended to ``spec["log"]`` (when the sandbox lets it): kind, mode, format, whether the request carries the
``reverseEngineering`` pointer, the paths written, deleted and denied.

Usage (as a provider command): python practice_agent.py --spec SPEC.json
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

RESULTS: dict[str, Any] = {
    "clarify": {"questions": []},
    "review": {"findings": []},
    "acceptance": {"tests": []},
    "locate": {"locations": [], "questions": []},
}
SURVEY = {
    "style": "custom",
    "summary": "No layering.",
    "layers": [],
    "allow": {},
    "organization": "None.",
    "components": [{"name": "app", "path": ".", "responsibility": "The project."}],
    "conventions": [],
    "guidance": ["Follow the structure."],
}


def ids(items: Any, *names: str) -> list[str]:
    found: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            for name in names:
                if item.get(name):
                    found.append(str(item[name]))
                    break
    return found


def architecture(spec: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    mode = request.get("mode")
    if mode == "advise":
        style = str(spec.get("adviseStyle") or "layered")
        return {
            "options": [
                {
                    "id": f"option-{style}",
                    "style": style,
                    "title": style,
                    "benefits": ["A known structure."],
                    "costs": ["Some ceremony."],
                    "fit": "The fixture recommends one style.",
                    "supports": ["A small project."],
                    "recommended": True,
                    "layers": [],
                    "allow": {},
                }
            ]
        }
    if mode == "organize":
        return {
            "summary": "Follow the layers of the chosen style.",
            "structure": [{"path": "src", "purpose": "The code."}],
            "practices": ["Keep dependencies pointing inwards."],
        }
    return dict(SURVEY)


def plan(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "subtasks": [
            {
                "title": str(task.get("title") or "The whole task"),
                "requirements": ids(task.get("requirements"), "requirementId", "requirement_id"),
                "criteria": ids(
                    task.get("acceptanceCriteria") or task.get("acceptance_criteria"),
                    "criterionId",
                    "criterion_id",
                ),
                "constraints": [],
            }
        ]
    }


def acceptance(spec: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    form = "gherkin" if request.get("format") == "gherkin" else "pytest"
    directory = str(request.get("directory") or "tests/acceptance")
    files = (spec.get("acceptance") or {}).get(form) or []
    return {
        "tests": [
            {"path": item["path"].replace("{directory}", directory), "content": item["content"]}
            for item in files
        ]
    }


def implement(spec: dict[str, Any]) -> dict[str, list[str]]:
    done: dict[str, list[str]] = {"written": [], "deleted": [], "denied": []}
    for path, content in (spec.get("write") or {}).items():
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Bytes, so the change is written with LF on every platform.
            target.write_bytes(content.encode("utf-8"))
            done["written"].append(path)
        except OSError as error:
            done["denied"].append(f"write {path}: {error.__class__.__name__}")
    for path in spec.get("delete") or []:
        try:
            Path(path).unlink(missing_ok=True)
            done["deleted"].append(path)
        except OSError as error:
            done["denied"].append(f"delete {path}: {error.__class__.__name__}")
    return done


def main() -> int:
    spec = json.loads(Path(sys.argv[sys.argv.index("--spec") + 1]).read_text(encoding="utf-8"))
    raw = sys.stdin.buffer.read().decode("utf-8")
    request = json.loads(raw)
    kind = str(request.get("kind", "implement"))
    panel = kind == "review" and "outputContract" in request
    task = request.get("task") if isinstance(request.get("task"), dict) else {}
    record: dict[str, Any] = {
        "kind": kind,
        "panel": panel,
        "mode": request.get("mode"),
        "format": request.get("format"),
        "reverseEngineering": '"reverseEngineering"' in raw,
        "requestChars": len(raw),
    }
    if kind == "implement":
        done = implement(spec)
        record.update(done)
        # Also in the summary the harness records with the invocation: the log lives outside
        # the workspace, where a write sandbox may refuse it.
        summary = "Change written " + json.dumps(done, sort_keys=True)
        answer: dict[str, Any] = {"status": "PASSED", "summary": summary[:500]}
    else:
        if panel:
            result: Any = {"verdict": "PASS", "findings": [], "summary": "No finding."}
        elif kind == "architecture":
            result = architecture(spec, request)
        elif kind == "acceptance":
            result = acceptance(spec, request)
            record["proposed"] = [item["path"] for item in result["tests"]]
        elif kind == "plan":
            result = plan(task)
        else:
            result = RESULTS.get(kind, {})
        answer = {"status": "PASSED", "summary": kind, "result": result}
    answer["usage"] = {
        "inputTokens": math.ceil(len(raw) / 4),
        "outputTokens": math.ceil(len(json.dumps(answer)) / 4),
    }
    log = spec.get("log")
    if log:
        try:
            with Path(log).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            pass  # outside the workspace a write sandbox may refuse it; the summary keeps it
    sys.stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
