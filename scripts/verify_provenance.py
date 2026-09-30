#!/usr/bin/env python3
"""Verify that the repository tree is byte-identical to the v0.8.0 thesis cut.

The reference is ``provenance/v0.8.0.sha256``: one ``<sha256>  <path>`` line per file of the
extracted cut (236 files). Binary release artifacts listed in ``EXCLUDED`` are not versioned;
they are attached to the ``v0.8.0`` GitHub release instead (see ``docs/provenance.md``).

Modes:

* ``full``: every non-excluded manifest path must exist and match (use on the ``v0.8.0`` tag).
* ``partial``: every manifest path that exists must match; missing paths are reported, not
  failed (use while the cut is being imported).
* ``release-assets DIR``: the excluded binaries found in DIR must match the manifest.

Exit code 0 means verified; 1 means at least one mismatch or missing required file.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "provenance" / "v0.8.0.sha256"
EXCLUDED = frozenset(
    {
        ".coverage.release",
        "dist/.gitignore",
        "dist/governed_agent_harness-0.8.0-py3-none-any.whl",
        "dist/governed_agent_harness-0.8.0.tar.gz",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        digest, sep, rel = line.partition("  ")
        if not sep or len(digest) != 64:
            raise SystemExit(f"{path}:{number}: malformed manifest line")
        entries[rel] = digest
    return entries


def verify_tree(manifest: dict[str, str], root: Path, *, full: bool) -> int:
    matched, mismatched, missing = 0, [], []
    for rel, expected in sorted(manifest.items()):
        if rel in EXCLUDED:
            continue
        target = root / rel
        if not target.is_file():
            missing.append(rel)
            continue
        if sha256_file(target) == expected:
            matched += 1
        else:
            mismatched.append(rel)
    required = len(manifest) - len(EXCLUDED & manifest.keys())
    print(f"manifest entries: {len(manifest)} (excluded binaries: {len(EXCLUDED & manifest.keys())})")
    print(f"matched: {matched}/{required}  mismatched: {len(mismatched)}  missing: {len(missing)}")
    for rel in mismatched:
        print(f"MISMATCH {rel}")
    for rel in missing:
        print(f"{'MISSING' if full else 'pending'} {rel}")
    if mismatched or (full and missing):
        return 1
    return 0


def verify_assets(manifest: dict[str, str], directory: Path) -> int:
    status = 0
    for rel in sorted(EXCLUDED):
        candidate = directory / Path(rel).name
        if not candidate.is_file():
            print(f"absent  {rel}")
            continue
        ok = sha256_file(candidate) == manifest[rel]
        print(f"{'OK      ' if ok else 'MISMATCH'} {rel}")
        status |= 0 if ok else 1
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("full", "partial", "release-assets"))
    parser.add_argument("directory", nargs="?", type=Path, help="asset directory for release-assets")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    if args.mode == "release-assets":
        if args.directory is None:
            parser.error("release-assets requires a directory")
        return verify_assets(manifest, args.directory)
    return verify_tree(manifest, args.root, full=args.mode == "full")


if __name__ == "__main__":
    sys.exit(main())
