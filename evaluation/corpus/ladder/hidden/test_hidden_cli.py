"""Hidden acceptance checks of part E (the command line), never shown to the implementer.

Run from the workspace root: the command line runs as ``python -m inventory.cli`` with ``src`` on
``PYTHONPATH``, on the corpus fixtures."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()


def run(*args):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run([sys.executable, "-m", "inventory.cli", *args], capture_output=True, text=True, env=env, cwd=ROOT)


def test_e_value_of_the_stock():
    proc = run("value", str(ROOT / "fixtures" / "stock.json"))
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"items": 2, "totalValue": "6.02"}


def test_e_value_of_an_empty_inventory():
    proc = run("value", str(ROOT / "fixtures" / "empty.json"))
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"items": 0, "totalValue": "0.00"}


def test_e_usage_error_exits_2():
    assert run("total").returncode == 2
