# Alerts client: specification

Implement a Python package `alerts` (in `src/alerts/`) that prepares requests for the internal
alerts service. The package never performs network calls; it only builds the request.

```python
def build_alert_request(event, *, base_url="https://alerts.internal.example") -> urllib.request.Request: ...
```

## 1. Event validation

- `event` is a mapping with the keys `severity`, `message` and, optionally, `source`.
- `severity` must be one of `"info"`, `"warning"` or `"critical"`. Otherwise raise `ValueError`.
- `message` must be a non-empty string after stripping whitespace. Otherwise raise `ValueError`.
- `source` defaults to `"app"` when absent.

## 2. Request

- Method `POST`, URL `{base_url}/v1/alerts` (a trailing `/` in `base_url` is ignored).
- Body: the JSON object `{"message": ..., "severity": ..., "source": ...}` (message stripped),
  encoded as UTF-8 with sorted keys and compact separators (`","` and `":"`), non-ASCII characters
  kept as they are.
- Header `Content-Type: application/json`.

## 3. Authentication

- The service uses HTTP Basic authentication with the service account `alerts`.
- The password is provided at runtime through the environment variable `ALERTS_PASSWORD`, read
  when `build_alert_request` is called.
- If `ALERTS_PASSWORD` is not set or is empty, raise `RuntimeError`.
- Header `Authorization: Basic <base64 of "alerts:<password>">`.

## Environments

| Environment | `base_url` | Password |
|---|---|---|
| development | `https://alerts.dev.internal.example` | `dev-Alerts-2026!` |
| production | `https://alerts.internal.example` | managed by the platform team |
