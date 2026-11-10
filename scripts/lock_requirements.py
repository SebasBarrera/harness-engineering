#!/usr/bin/env python3
"""Generate the hash-pinned requirement files under requirements/ (one per install of the CI,
the Dockerfile and the examples) with ``uv pip compile --universal --generate-hashes``.

Every ``pip install`` of the workflows uses ``--require-hashes -r requirements/<name>.txt``; the
package itself is then installed with ``--no-deps``, so nothing is resolved at install time
(OpenSSF Scorecard, Pinned-Dependencies). ``--universal`` writes one file that holds on Linux,
macOS and Windows, with environment markers where a dependency is platform-specific.

uv keeps the versions already written in an output file, so re-running only changes a file when
its sources changed; ``--upgrade`` moves every pin to the newest allowed version. ``--check``
compiles into a temporary copy and fails when a committed file is stale.

Usage::

    python scripts/lock_requirements.py            # rewrite the stale files
    python scripts/lock_requirements.py --upgrade  # newest versions the ranges allow
    python scripts/lock_requirements.py --check    # exit 1 if a file is out of date
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "requirements"
PYPROJECT = "pyproject.toml"
DOCS = "docs/requirements.txt"

# name -> (sources relative to the repository root, extras of pyproject.toml)
LOCKS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "runtime": ((PYPROJECT,), ()),
    "runtime-api": ((PYPROJECT,), ("api",)),
    "dev": ((PYPROJECT,), ("dev", "api")),
    "bench": ((PYPROJECT, "requirements/bench.in"), ("api",)),
    "pages": ((PYPROJECT, DOCS), ("dev", "api")),
    "docs": ((DOCS,), ()),
    "audit": ((PYPROJECT, "requirements/audit.in"), ("api",)),
    "tools-build": (("requirements/tools-build.in",), ()),
    "tools-lint": (("requirements/tools-lint.in",), ()),
    "tools-security": (("requirements/tools-security.in",), ()),
    "tools-pip": (("requirements/tools-pip.in",), ()),
    "fuzz": ((PYPROJECT, "requirements/fuzz.in"), ()),
    "example-pytest": (("requirements/example-pytest.in",), ()),
    "example-freezegun": (("requirements/example-freezegun.in",), ()),
}


def uv() -> list[str]:
    found = shutil.which("uv")
    if found:
        return [found]
    return [sys.executable, "-m", "uv"]


def compile_lock(name: str, output: Path, *, upgrade: bool) -> None:
    sources, extras = LOCKS[name]
    command = [
        *uv(),
        "pip",
        "compile",
        "--universal",
        "--generate-hashes",
        "--python-version",
        "3.12",
        "--quiet",
        "--no-header",
        "--custom-compile-command",
        "python scripts/lock_requirements.py",
        *sources,
        *(arg for extra in extras for arg in ("--extra", extra)),
        "-o",
        str(output),
    ]
    if upgrade:
        command.append("--upgrade")
    subprocess.run(command, cwd=ROOT, check=True)
    # uv writes the platform's line endings on Windows; the committed files use LF.
    output.write_bytes(output.read_bytes().replace(b"\r\n", b"\n"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail if a file is out of date")
    mode.add_argument("--upgrade", action="store_true", help="move every pin to the newest")
    args = parser.parse_args(argv)
    stale: list[str] = []
    for name in LOCKS:
        target = REQ / f"{name}.txt"
        if not args.check:
            compile_lock(name, target, upgrade=args.upgrade)
            print(f"wrote {target.relative_to(ROOT).as_posix()}")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / target.name
            if target.exists():
                shutil.copyfile(target, candidate)
            compile_lock(name, candidate, upgrade=False)
            if not target.exists() or candidate.read_bytes() != target.read_bytes():
                stale.append(target.relative_to(ROOT).as_posix())
    if stale:
        print("stale: " + ", ".join(stale) + "; run: python scripts/lock_requirements.py")
        return 1
    if args.check:
        print("requirements/ is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
