#!/usr/bin/env python3
"""Run the harness CLI with its clock moved forward (for the expired-decision probe).

``python timeshift.py --hours 73 -- run continue --run RUN`` replaces ``datetime.datetime`` with a
subclass whose ``now()`` and ``utcnow()`` are shifted by the given hours, then imports and runs
``governed_harness.cli.main``. Only the wall clock of this one process moves; nothing is written
to the record besides what the command itself records. Must run with the harness's interpreter.
"""

from __future__ import annotations

import datetime as _dt
import sys


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) < 3 or argv[0] != "--hours" or "--" not in argv:
        sys.stderr.write("usage: timeshift.py --hours N -- HARNESS-ARGS...\n")
        return 2
    shift = _dt.timedelta(hours=float(argv[1]))
    real = _dt.datetime

    class Shifted(real):  # type: ignore[misc,valid-type]
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return real.now(tz) + shift

        @classmethod
        def utcnow(cls):  # type: ignore[no-untyped-def]
            return real.utcnow() + shift

    _dt.datetime = Shifted  # type: ignore[misc]
    sys.argv = ["harness", *argv[argv.index("--") + 1 :]]
    from governed_harness.cli.main import main as harness_main

    harness_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
