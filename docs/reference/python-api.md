# Python API

The CLI and the local API are thin layers over `HarnessApplication`. Scripts that need the same
behavior (for example `scripts/metrics_report.py`) should use it instead of reading the SQLite
state directly. The package ships a `py.typed` marker. The API below is generated from the source
code and follows the version of this documentation; it is not a stability guarantee for the
research beta.

::: governed_harness.application.HarnessApplication

## Exit-code errors

::: governed_harness.domain.errors
