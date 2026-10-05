# Local API and dashboard

`harness api serve --path <project>` starts a FastAPI application (requires the `api` extra) over
the same application layer as the CLI. It serves one project.

> [!WARNING]
> There is **no authentication, no roles and no multi-user model**. The decision endpoint accepts
> any actor string outside the agent, validator and harness namespaces. Keep the server on the
> loopback default (`--host 127.0.0.1`) or behind a separately secured environment. See [SECURITY.md](https://github.com/SebasBarrera/harness-engineering/blob/develop/SECURITY.md) and issue #18.

```bash
harness api serve --path . --host 127.0.0.1 --port 8765
```

## Routes

| Method | Path | Returns | Errors |
|---|---|---|---|
| `GET` | `/api/health` | The `doctor` result for the project (`status`, checks) | — |
| `GET` | `/api/runs` | Runs of the project, newest first | 400 |
| `GET` | `/api/runs/{run}` | Status projection: execution, phases, validation summary, findings summary, gate, human decision, event count, event-chain check, metrics | 404 |
| `GET` | `/api/runs/{run}/review?diff=false` | The decision brief (what was asked, what changed, risks, what was verified on which digest and what was not, exceptions, history, delta since the last decision, next commands); `diff=true` adds the redacted diff | 404 |
| `GET` | `/api/inbox` | Runs waiting for a person (decision or clarification), oldest first | 400 |
| `GET` | `/api/exceptions?status=all\|active\|expired` | The exception ledger (`review.exceptions`) | 400 |
| `GET` | `/api/runs/{run}/trace?format=json\|markdown\|jsonl\|sarif` | The trace in the requested format (`application/json`, `text/markdown`, `application/x-ndjson`, `application/sarif+json`); default `json` | 404, 409 (run does not verify under `governance.verifyRecords`) |
| `GET` | `/api/runs/{run}/evidence` | Evidence records with artifact references | 404 |
| `GET` | `/api/runs/{run}/findings` | Structured findings | 404 |
| `GET` | `/api/runs/{run}/retrospective` | Non-mutating retrospective | 404 |
| `POST` | `/api/runs/{run}/decision` | The recorded decision and the updated execution | 403, 409, 422 |
| `GET` | `/` | The embedded dashboard (HTML) | — |

The thesis cut had nine routes: seven queries, one decision `POST` and the dashboard; the inbox,
exceptions and review routes were added in 1.1 (#53). `{run}` also accepts `latest` and a unique
prefix of a run id. The OpenAPI document is available at `/openapi.json` and the interactive docs at
`/docs` (FastAPI defaults).

## Recording a decision

The request body uses snake_case keys and rejects unknown keys (a camelCase body returns 422):

```bash
curl -s -X POST http://127.0.0.1:8765/api/runs/<run>/decision \
  -H 'Content-Type: application/json' \
  -d '{"decision": "APPROVE",
       "change_set_digest": "sha256:<current digest from GET /api/runs/<run>>",
       "actor_id": "human.reviewer",
       "rationale": "Acceptance criteria and evidence reviewed",
       "continue_after": true}'
```

| Field | Type | Default |
|---|---|---|
| `decision` | `APPROVE`, `APPROVE_EXCEPTION`, `REQUEST_CHANGES`, `REJECT` | required |
| `change_set_digest` | string | required |
| `rationale` | string | required |
| `actor_id` | string | the Git user under `governance.deciderIdentity: git` (`human.web` with a `warnings` entry in the response when Git has no identity), otherwise `human.web` |
| `continue_after` | boolean | `true` |
| `expires_in`, `expires_at`, `scope`, `alternative_evidence`, `follow_up` | exception options of `APPROVE_EXCEPTION` under `review.exceptions` (see [configuration](configuration.md#exceptions)) | none |

Every policy violation of `harness gate decide` (stale digest, `APPROVE` over a gate that did not
pass, run not in `DECISION`, exception without rationale) is returned as **409** with the reason in
`detail`, for example `{"detail": "human decisions are accepted only in DECISION"}`. An
`actor_id` in the namespace of an agent (`agent.*`), a validator (`validator.*`) or the harness
(`harness.*`) is refused with **403** whatever the configuration.

With `governance.trustedHosts` (written by `harness init`: `127.0.0.1`, `localhost`, `::1`) every
route answers **400** to a request whose `Host` header is not one of those names, so a web page
cannot reach the server through DNS rebinding. Without the key every host is accepted, as in 1.0.0.

## Dashboard

The page served at `/` lists the runs, shows the decision brief of the selected run (the full
status projection is under a collapsed section) and lets a person record `APPROVE`,
`REQUEST_CHANGES`, `APPROVE_EXCEPTION` or `REJECT` against the current ChangeSet digest, with an
actor and a rationale; a confirmation shows the decision, the digest and the files before it is
sent. Its left column lists what waits for a person (`/api/inbox`) and the page refreshes every
5 seconds without discarding a rationale being typed. It contains no policy or workflow logic: it calls the routes
above. The static prototype that lived in `web/` targeted routes that never existed and was
removed in v0.8.1 (issue #12).
