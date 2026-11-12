"""Formateador de reportes heredado (lo usa el equipo de finanzas tal cual)."""

import os
import sys


def report_line(name, amount):
    return "%-20s %10.2f" % (name, amount)


def write_report(rows, out=sys.stdout):
    for name, amount in rows:
        out.write(report_line(name, amount) + "\n")
