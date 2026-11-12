"""Formateador de reportes heredado (lo usa el equipo de finanzas tal cual)."""

import sys
from collections.abc import Iterable
from typing import TextIO


def report_line(name: str, amount: float) -> str:
    return f"{name:<20} {amount:10.2f}"


def write_report(rows: Iterable[tuple[str, float]], out: TextIO = sys.stdout) -> None:
    for name, amount in rows:
        out.write(report_line(name, amount) + "\n")
