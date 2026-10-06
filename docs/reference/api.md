# Local API and dashboard

`harness api serve --path <project>` starts a FastAPI application (requires the `api` extra) over
the same application layer as the CLI. It serves one project.

> [!WARNING]
> Since 2.0 (#18) the `api` section of `project.yaml`, which `harness init` writes, requires a
> bearer token on every route and gives each person a role. **A project without that section has
> no authentication, no roles and no multi-user model, as in 1.0.0**: anyone who reaches the port
> can read every run and record a decision under any actor id outside the agent, validator and
> harness namespaces. Either way, keep the server on the loopback default (`--host 127.0.0.1`):
> the token travels over plain HTTP and the harness is not a sandbox for the host it runs on. See
> [SECURITY.md](https://github.com/SebasBarrera/harness-engineering/blob/develop/SECURITY.md).

```bash
harness api serve --path . --host 127.0.0.1 --port 8765
```

## Authentication and roles

With the `api` section (and `api.auth` not `off`), every request, the dashboard page and
`/openapi.json` included, must carry `Authorization: Bearer TOKEN`; without a valid token the
answer is **401** with `WWW-Authenticate: Bearer`. The token is accepted in that header only
(never in a query string, which servers log) and compared in constant time.

| Role | May |
|---|---|
| `viewer` | Every `GET`: the dashboard, runs, briefs, traces, evidence, findings, inbox, registry |
| `reviewer` | What a viewer may, and record decisions (`POST /api/runs/{run}/decision`) |
| `admin` | What a reviewer may, and read the effective configuration (`GET /api/config`) |

A request whose role is not enough is answered **403**. Tokens never appear in `project.yaml`:

```yaml
api:
  auth: token                 # token (the default when the section is present) or off
  tokenEnv: HARNESS_API_TOKEN # the variable holding the token of the person who starts the server
  tokenUser: human.web        # the actor id recorded on that person's decisions
  tokenRole: admin            # that person's role
  users:                      # further people: id, role and the NAME of their token's variable
    - id: alice
      role: reviewer
      tokenEnv: HARNESS_API_TOKEN_ALICE
```

| Key | Default | Meaning |
|---|---|---|
| `auth` | `token` with the section, no authentication without it | `off` serves without authentication, as in 1.0.0 |
| `tokenEnv` | `HARNESS_API_TOKEN` | Variable holding the start token; when it is not set, `harness api serve` generates one |
| `tokenUser` | `human.web` | Actor id of the start token (a person: not `agent.*`, `validator.*` or `harness.*`) |
| `tokenRole` | `admin` | Role of the start token |
| `users` | none | People with an `id` (an actor id), a `role` and a `tokenEnv` |

**How the token is shown.** When `tokenEnv` is set, `harness api serve` uses its value and prints
only the variable's name. When it is not set, the server generates a random token (Python
`secrets`, 32 bytes) and prints it **once, on standard error, at start**; it is not written to any
file, event, artifact, audit record or log. Set `tokenEnv` to choose the token yourself, for
example when standard error is captured by a log. A user whose variable is not set cannot sign in
(a warning names the user); a token shorter than 16 characters, two people sharing one token, a
user id in the agent, validator or harness namespace, an invalid `api` section and a
`project.yaml` that cannot be read stop the server before it listens (fail closed). The values of every token variable are redacted from stored artifacts.

**Decisions.** The authenticated person is the decider: the request's `actor_id` must be absent
(or empty) or equal to their user id, otherwise **403**. Each decision request appends an
`attempted` record, before anything is decided, and then a `refused` or `recorded` record to
`.harness/audit/api-decisions.jsonl` (mode 0600, append-only): time, user id, role, route, run,
decision, ChangeSet digest, requested actor id, client host, outcome, HTTP status and the reason or
the decision id. If the attempt cannot be written, nothing is decided (**500**). The log never
holds a token. Without authentication no audit log is written, as in 1.0.0.

**Dashboard.** Without a token `GET /` answers 401 with a sign-in page that asks for the token,
keeps it in the tab's `sessionStorage` and loads the dashboard with it. Every call of the dashboard
goes through one `request()` helper that sends the token as `Authorization: Bearer` and signs out
when the server refuses it. Closing the tab forgets the token.

`harness config validate` shows the effective settings under `api` (variable names and whether
each is set, never a value).

## Routes

| Method | Path | Returns | Errors |
|---|---|---|---|
| `GET` | `/api/health` | The `doctor` result for the project (`status`, checks) | — |
| `GET` | `/api/session` | Who the request is authenticated as (`userId`, `role`), or `{"authentication": "off"}` (#18) | 401 |
| `GET` | `/api/config` | The `harness config validate` result; role `admin` (#18) | 400, 401, 403 |
| `GET` | `/api/runs` | Runs of the project, newest first | 400 |
| `GET` | `/api/runs/{run}` | Status projection: execution, phases, validation summary, findings summary, gate, human decision, event count, event-chain check, metrics | 404 |
| `GET` | `/api/runs/{run}/review?diff=false` | The decision brief (what was asked, what changed, risks, what was verified on which digest and what was not, exceptions, history, delta since the last decision, next commands); `diff=true` adds the redacted diff | 404 |
| `GET` | `/api/inbox` | What waits for a person, oldest first: a decision, clarification answers and, since #55, a deferred verification waiting for evidence or a preflight waiting for a decision and, since #73, every other wait before DECISION (`plan`, `decomposition`, `acceptance`, `architecture`, `contract`, with the digest and the answering command) | 400 |
| `GET` | `/api/runs/{run}/verification` | The verification plan, preflight, certification, deferred items and checklist of a run (#55) | 404 |
| `GET` | `/api/registry` | The projects of the run registry outside the workspaces (`runtime.stateDir`) with their latest runs (#55) | 400 |
| `GET` | `/api/exceptions?status=all\|active\|expired` | The exception ledger (`review.exceptions`) | 400 |
| `GET` | `/api/runs/{run}/trace?format=json\|markdown\|jsonl\|sarif` | The trace in the requested format (`application/json`, `text/markdown`, `application/x-ndjson`, `application/sarif+json`); default `json` | 404, 409 (run does not verify under `governance.verifyRecords`) |
| `GET` | `/api/runs/{run}/evidence` | Evidence records with artifact references | 404 |
| `GET` | `/api/runs/{run}/findings` | Structured findings | 404 |
| `GET` | `/api/runs/{run}/retrospective` | Non-mutating retrospective | 404 |
| `GET` | `/api/metrics?since=&task=&model=&agent=&all_repos=false` | The report of `harness metrics --format json` (#58): computed from the records, no model call | 400 |
| `GET` | `/api/metrics/report` (same parameters) | The self-contained HTML report of `harness metrics --format html` (#58) | 400 |
| `POST` | `/api/runs/{run}/decision` | The recorded decision and the updated execution | 403, 409, 422 |
| `GET` | `/` | The embedded dashboard (HTML) | — |

The thesis cut had nine routes: seven queries, one decision `POST` and the dashboard; the inbox,
exceptions and review routes were added in 2.0 (#53), the session and configuration routes with
authentication (#18). Under authentication every route also answers 401 without a valid token,
and the decision route 403 without the role `reviewer`. `{run}` also accepts `latest` and a unique
prefix of a run id. The OpenAPI document is available at `/openapi.json` and the interactive docs at
`/docs` (FastAPI defaults).

## Recording a decision

The request body uses snake_case keys and rejects unknown keys (a camelCase body returns 422):

```bash
curl -s -X POST http://127.0.0.1:8765/api/runs/<run>/decision \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $HARNESS_API_TOKEN" \
  -d '{"decision": "APPROVE",
       "change_set_digest": "sha256:<current digest from GET /api/runs/<run>>",
       "rationale": "Acceptance criteria and evidence reviewed",
       "continue_after": true}'
```

| Field | Type | Default |
|---|---|---|
| `decision` | `APPROVE`, `APPROVE_EXCEPTION`, `REQUEST_CHANGES`, `REJECT` | required |
| `change_set_digest` | string | required |
| `rationale` | string | required |
| `actor_id` | string | under authentication, the authenticated user (any other value is refused with 403); without it, the Git user under `governance.deciderIdentity: git` (`human.web` with a `warnings` entry in the response when Git has no identity), otherwise `human.web` |
| `continue_after` | boolean | `true` |
| `expires_in`, `expires_at`, `scope`, `alternative_evidence`, `follow_up` | exception options of `APPROVE_EXCEPTION` under `review.exceptions` (see [configuration](configuration.md#exceptions)) | none |
| `checked_items` | checklist items the person verified (`review.manualChecklist`, see [configuration](configuration.md#manual-checklist-and-attachments)) | none |

Every policy violation of `harness gate decide` (stale digest, `APPROVE` over a gate that did not
pass, run not in `DECISION`, exception without rationale) is returned as **409** with the reason in
`detail`, for example `{"detail": "human decisions are accepted only in DECISION"}`. An
`actor_id` in the namespace of an agent (`agent.*`), a validator (`validator.*`) or the harness
(`harness.*`) is refused with **403** whatever the configuration.

With `governance.trustedHosts` (written by `harness init`: `127.0.0.1`, `localhost`, `::1`) every
route answers **400** to a request whose `Host` header is not one of those names, so a web page
cannot reach the server through DNS rebinding. Without the key every host is accepted, as in 1.0.0.
The host check runs before the authentication.

## Dashboard

The page served at `/` (behind the sign-in page under authentication) shows who is signed in,
lists the runs, shows the decision brief of the selected run (the full
status projection is under a collapsed section) and lets a person record `APPROVE`,
`REQUEST_CHANGES`, `APPROVE_EXCEPTION` or `REJECT` against the current ChangeSet digest, with an
actor and a rationale; a confirmation shows the decision, the digest and the files before it is
sent. Its left column lists what waits for a person (`/api/inbox`) and the page refreshes every
5 seconds without discarding a rationale being typed. Since #55 the brief shows the certification
and the deferred items, the decision form lists the checklist items to tick, and a section lists
the repositories of the run registry (`/api/registry`). It contains no policy or workflow logic: it calls the routes
above. Since #58 a Metrics tab shows the totals of `/api/metrics` and the self-contained report of
`/api/metrics/report`, fetched with the same token and shown in a frame from its text (optionally
for every repository of the registry). The static prototype that lived in `web/` targeted routes that never existed and was
removed in v0.8.1 (issue #12).
