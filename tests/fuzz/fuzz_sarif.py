#!/usr/bin/env python3
"""Fuzz the SARIF reader with provenance (checks.sarif.read_sarif).

A tool's SARIF report is agent-influenced input: invalid JSON or a document that is not SARIF
yields [] and the reader never raises; each result names a tool."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import atheris

with atheris.instrument_imports():
    from governed_harness.checks.sarif import read_sarif

WORKSPACE = Path("/fuzz-workspace")


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    if fdp.ConsumeBool():
        text = fdp.ConsumeUnicodeNoSurrogates(fdp.remaining_bytes())
    else:
        # A structurally plausible document whose fields are fuzzed, to reach past json.loads.
        uri = fdp.ConsumeUnicodeNoSurrogates(64)
        line = fdp.ConsumeIntInRange(-5, 10_000)
        document = {
            "version": "2." + str(fdp.ConsumeIntInRange(0, 9)),
            "runs": [
                {
                    "tool": {"driver": {"name": fdp.ConsumeUnicodeNoSurrogates(16)}},
                    "results": [
                        {
                            "ruleId": fdp.ConsumeUnicodeNoSurrogates(16),
                            "ruleIndex": fdp.ConsumeIntInRange(-2, 5),
                            "level": fdp.ConsumeUnicodeNoSurrogates(8),
                            "message": {"text": fdp.ConsumeUnicodeNoSurrogates(64)},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": uri},
                                        "region": {"startLine": line},
                                    }
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        text = json.dumps(document)
    results = read_sarif(text, WORKSPACE)
    assert isinstance(results, list)
    for result in results:
        assert result.tool, result


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
