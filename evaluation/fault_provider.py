#!/usr/bin/env python3
"""Deterministic command provider for the fault-injection probes.

It applies the reference solution of the brownfield task and then one named fault, so that each
control of the harness can be exercised on a real repository with a known defect.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENCODING = Path("src/itsdangerous/encoding.py")
TESTS = Path("tests/test_itsdangerous/test_encoding.py")
NEW_TEST = '''

def test_strict_base64_rejects_characters_outside_the_alphabet():
    for value in ("YW+j", "YW/j", "YW j", "YWJj\\u00e9"):
        with pytest.raises(BadData):
            base64_decode(value)
'''


def apply_reference() -> None:
    ENCODING.write_text((HERE / "reference" / "brownfield-itsdangerous-encoding.py").read_text())
    with TESTS.open("a", encoding="utf-8") as handle:
        handle.write(NEW_TEST)


def append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


FAULTS = {
    "correct": lambda: None,
    "regression": lambda: ENCODING.write_text(
        ENCODING.read_text().replace('return base64.urlsafe_b64encode(string).rstrip(b"=")',
                                     "return base64.urlsafe_b64encode(string)")
    ),
    "failing-test": lambda: append(TESTS, '\n\ndef test_probe_expectation():\n    assert base64_decode("YWJj") == b"abd"\n'),
    "secret": lambda: append(ENCODING, '\n_service_password = "probe-Secret-2026"\n'),
    "dynamic-eval": lambda: append(ENCODING, '\n_decoder = eval("base64.urlsafe_b64decode")\n'),
    "todo": lambda: append(ENCODING, "\n# TODO: review the padding rules with the maintainers\n"),
    "out-of-scope": lambda: append(Path("README.md"), "\nStrict base64 decoding.\n"),
    "output-flood": lambda: sys.stderr.write("x" * (64 * 1024 * 1024)),
    "timeout": lambda: time.sleep(120),
    "missing-tool": lambda: None,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fault", choices=sorted(FAULTS), required=True)
    args = parser.parse_args()
    json.load(sys.stdin)
    apply_reference()
    FAULTS[args.fault]()
    print(json.dumps({"status": "PASSED", "summary": f"reference change with fault '{args.fault}'"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
