# Minimal web dashboard

The dashboard is served by `harness api serve --path <workspace>`. It consumes the same application layer as the CLI and contains no gate, policy, workflow or persistence logic. The implementation is embedded in `governed_harness.api.app` so the wheel remains self-contained.
