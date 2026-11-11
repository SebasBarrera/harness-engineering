"""The verification ladder (since 2.0, #55): deterministic building blocks.

Pure functions and small value types with no access to the engine, the state store or the
configuration resolver: a JSON path subset for probe assertions, the evaluation of probe
output, hunk reversal for light mutation, the reading of external evidence (JUnit, SARIF, CI
status), the verification capabilities of the technology profiles, the operational contract and
the lint of agent instruction files. The orchestration (``orchestration.ladder``) wires them
into the phases.

``ladder.jsonpath`` imports nothing of the harness: the domain models use it to validate probe
assertions, so it must not import them back."""
