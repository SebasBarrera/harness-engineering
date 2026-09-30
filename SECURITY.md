# Security model

## Trust assumptions

The implementation distinguishes authorization logic from strong containment.

| Element | Trust treatment |
|---|---|
| Released core and built-in primitive adapters | Trusted computing base for the local prototype |
| Locked organizational policy | Trusted only after independent distribution/integrity controls |
| Project configuration | Validated input; cannot weaken locked policies |
| Repository files, names and documentation | Untrusted data and possible prompt-injection content |
| External plugins and native tools | Untrusted processes constrained by declared capabilities, but not strongly sandboxed by the local runner |
| Agent/model output | Untrusted proposal; schema and independent evidence required |

## Enforced in the research beta

- Deny-by-default capability records bound to the exact actor.
- Capability scopes, expiration and optional approval-required flags.
- Command authorization by exact executable/prefix.
- Structured argument vectors with `shell=False`.
- Working-directory containment beneath the configured workspace.
- Path traversal and existing-symlink rejection for harness filesystem operations.
- Atomic structured patches with optional expected-content digest.
- No delete operation unless explicitly enabled by the patch caller.
- Timeouts, cancellation and process-group termination.
- Bounded stdout/stderr capture.
- Secret redaction before logs, diffs or outputs enter the artifact store.
- Security review over the ephemeral unredacted diff; only redacted evidence is persisted.
- Content-addressed artifact verification.
- Per-execution event sequence and SHA-256 hash-chain verification.
- Mandatory checks fail closed when missing, blocked, timed out, malformed or inconclusive.
- Review findings have typed severities; policy computes whether they block.
- Human decision records include actor, rationale, gate, configuration/policy digests and exact ChangeSet digest.
- Any later owned-path change invalidates the approval.
- Retrospective recommendations cannot apply themselves.
- External plugin output must be a single schema-valid response correlated to the request ID.

## Not enforced by the local process runner

The local runner is **not an OS sandbox**. Once a native process is launched, capability metadata alone cannot technically prevent that process from:

- accessing files permitted by the operating-system user but outside the workspace;
- opening network connections;
- spawning additional processes;
- consuming CPU or memory beyond Python-level time/output controls;
- reading inherited OS resources not explicitly removed by the environment.

Therefore:

- use only trusted commands in controlled fixtures;
- bind the web server to localhost;
- do not expose secrets through environment variables or command arguments;
- use a container, VM, seccomp/App Sandbox/Windows sandbox or comparable runtime adapter before executing hostile repository scripts or third-party plugins;
- run the harness under a dedicated least-privileged OS identity for higher-risk evaluation.

## Approval and exception safety

`APPROVE` is rejected when the gate itself failed because of blocking findings. `APPROVE_EXCEPTION` is a distinct, auditable decision and still requires the exact current digest and rationale. Exceptions do not rewrite policy or alter historical evidence.

The current local decision API has no authentication. It is designed only for loopback use during research. Production adoption requires authenticated identity, authorization, CSRF protection, audit retention and transport security.

## Secret handling

The artifact redactor recognizes common authorization headers, secret assignments, GitHub-style tokens, AWS access keys and private-key blocks. This is defense in depth, not proof that all sensitive values are recognized. Callers may pass explicit secret literals for redaction.

Persist prompt/model content only when a reviewed retention policy explicitly permits it. The default design favors prompt/context digests and provider-reported usage metadata.

## Security test inventory

Automated tests cover:

- traversal and symlink escape;
- inert shell metacharacters;
- timeout/cancellation/output truncation;
- secret redaction and artifact tampering;
- event-chain tampering;
- stale approvals;
- blocking security findings and explicit exception approval;
- malformed plugin protocol responses;
- ownership isolation of ChangeSets.

## Responsible use

Use synthetic repositories and credentials in demonstrations. Never run the research beta against confidential code or production secrets without a reviewed isolation and retention design.
