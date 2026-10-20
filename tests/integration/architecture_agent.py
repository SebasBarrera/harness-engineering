"""A fixture agent for the architecture catalogue and the reverse engineering documents (#97).

It is a Python script (no model) that logs every request and answers the ``architecture`` calls
in all their modes; a variant file next to the log makes it answer a malformed result."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.integration.test_engineering_flow import configure

AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
kind = request.get("kind", "implement")
variant_file = Path(LOG + ".variant")
variant = variant_file.read_text() if variant_file.exists() else ""

def answer(result):
    print(json.dumps({"status": "PASSED", "summary": kind, "result": result}))
    sys.exit(0)

if kind == "clarify":
    answer({"questions": []})
if kind == "review":
    answer({"findings": []})
if kind == "acceptance":
    answer({"tests": []})
if kind == "architecture":
    mode = request.get("mode")
    if mode == "advise":
        option = {
            "id": "hex", "style": "hexagonal", "title": "Hexagonal core",
            "benefits": ["testable core"], "costs": ["more interfaces"], "recommended": True,
            "fit": "Business rules must be tested without a database.",
            "supports": ["pricing rules of its own", "a database that may change"],
            "layers": [{"name": "core", "paths": ["src/app/core/**"]},
                       {"name": "adapters", "paths": ["src/app/adapters/**"]}],
            "allow": {"adapters": ["core"]},
        }
        other = {
            "id": "lay", "style": "layered", "title": "Layered", "benefits": ["simple"],
            "costs": ["coupling"], "recommended": False, "fit": "A small team.",
            "supports": ["one deployable unit"], "layers": [], "allow": {},
        }
        if variant == "no-supports":
            del option["supports"]
        answer({"options": [option, other]})
    if mode == "organize":
        answer({"summary": "Core and adapters.",
                "structure": [{"path": "src/app/core", "purpose": "business rules"},
                              {"path": "src/app/adapters", "purpose": "infrastructure"}],
                "practices": ["ports belong to the core"]})
    result = {"style": "layered", "summary": "A pricing core and an infrastructure layer.",
              "layers": [{"name": "core", "paths": ["src/sample/pricing.py"]},
                         {"name": "infra", "paths": ["src/sample/infra/**"]}],
              "allow": {"infra": ["core"]}}
    if variant != "bare-survey":
        result.update({
            "organization": "Code lives in src/sample; infrastructure in src/sample/infra.",
            "components": [{"name": "pricing", "path": "src/sample/pricing.py",
                            "responsibility": "Applies discounts."}],
            "conventions": ["Type hints everywhere", "Tests in tests/ named test_*.py"],
            "guidance": ["Put new infrastructure code under src/sample/infra"],
        })
    answer(result)
for path, text in FILES.items():
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
print(json.dumps({"status": "PASSED", "summary": "implemented"}))
"""


def install(
    workspace: Path,
    tmp_path: Path,
    files: dict[str, str],
    sections: dict[str, Any],
    variant: str = "",
) -> Path:
    """Configure the fixture provider (as the wave 6 tests do) and swap its script for this
    agent; returns the log of the requests it received."""
    log = configure(workspace, tmp_path, files, sections)
    script = AGENT.replace("LOG", repr(str(log))).replace("FILES", repr(files))
    (tmp_path / "agent.py").write_text(script, encoding="utf-8")
    if variant:
        Path(f"{log}.variant").write_text(variant, encoding="utf-8")
    return log
