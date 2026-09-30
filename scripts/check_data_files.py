#!/usr/bin/env python3
"""Fail if any tracked YAML or JSON file does not parse.

YAML files are loaded with ``yaml.safe_load_all`` (multi-document aware); JSON files with the
standard library. Used by the lint workflow.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def tracked(*patterns: str) -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", *patterns], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode("utf-8")
    return [ROOT / name for name in output.split("\0") if name]


def main() -> int:
    errors: list[str] = []
    yaml_files = tracked("*.yml", "*.yaml")
    json_files = tracked("*.json")
    for path in yaml_files:
        try:
            list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except yaml.YAMLError as error:
            errors.append(f"{path.relative_to(ROOT)}: {error}")
    for path in json_files:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            errors.append(f"{path.relative_to(ROOT)}: {error}")
    for message in errors:
        print(f"INVALID {message}")
    print(f"parsed {len(yaml_files)} YAML and {len(json_files)} JSON files: {len(errors)} invalid")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
