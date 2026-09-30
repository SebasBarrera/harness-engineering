# Validation report

Date: 2026-08-30

This report records the checks executed before packaging the starter repository.

## Results

| Check | Command | Result |
|---|---|---|
| Core unit, contract and security tests | `PYTHONPATH=src pytest -q -p no:cacheprovider` | 18 passed |
| CLI health check | `PYTHONPATH=src python -m governed_harness doctor --json` | PASSED |
| Python fixture | `PYTHONPATH=tests/fixtures/python-project/src pytest -q -p no:cacheprovider tests/fixtures/python-project/tests` | 1 passed |
| Node fixture | `npm test -- --test-reporter=spec` | 1 passed |
| Node syntax check | `npm run lint` | passed |
| Node profile type-check placeholder | `npm run typecheck` | passed |
| JSON Schema contracts | Included in the core contract test suite | 18 schemas accepted as Draft 2020-12 |

## Scope of the result

These checks validate the foundational contracts, state transitions, local stores, gate semantics, path containment and two technology fixtures. They do not claim production-grade sandboxing, a complete agent integration, broad platform portability or completion of the full MVP.
