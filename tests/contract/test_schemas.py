from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).parents[2] / "schemas" / "v1"


def test_all_schemas_are_valid_draft_2020_12() -> None:
    schemas = list(SCHEMA_DIR.glob("*.schema.json"))
    assert len(schemas) >= 16
    for path in schemas:
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_normalized_status_enum_is_closed() -> None:
    common = json.loads((SCHEMA_DIR / "common.schema.json").read_text(encoding="utf-8"))
    assert common["$defs"]["status"]["enum"] == [
        "PENDING",
        "RUNNING",
        "PASSED",
        "FAILED",
        "BLOCKED",
        "SKIPPED",
        "NOT_APPLICABLE",
        "CANCELLED",
        "TIMED_OUT",
        "ERROR",
        "INCONCLUSIVE",
        # Since 2.0, only under governance.workspaceLease (#47).
        "INTERRUPTED",
    ]
