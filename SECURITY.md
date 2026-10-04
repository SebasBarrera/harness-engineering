# Security policy

## Reporting a vulnerability

Report vulnerabilities **privately** through GitHub:
[Security → Report a vulnerability](https://github.com/SebasBarrera/harness-engineering/security/advisories/new).
Do not open a public issue for a vulnerability. Include the affected version or commit, the steps to
reproduce and the impact. The maintainer acknowledges reports as soon as possible; this is a
single-maintainer research project, so there is no guaranteed response time.

Credentials or personal data committed by mistake are handled the same way: report them privately
so they can be rotated or removed before they are discussed publicly.

## Supported versions

| Version | Status |
|---|---|
| `develop` and the latest `v0.8.x` release | Security fixes |
| `v0.8.0` | Frozen thesis cut: vulnerabilities are documented and fixed in later releases, never in the tag |

## This is not a sandbox

The harness enforces **application-level** controls: capabilities, path containment, shell-free
process execution, bounded output, redaction, digest-bound decisions. It does not isolate the
processes it launches. An authorized command runs with the file-system, network, CPU and memory
permissions of the operating-system user. The local API and dashboard have no authentication and
must stay on loopback. Run untrusted repositories, agents or plugins only inside a container, VM or
comparable OS-level sandbox. The model below details what is and is not enforced.

One OS-level control exists, for agent providers only: with `runtime.agentSandbox: enforce` (written
by `harness init`) a command provider runs under `sandbox-exec` (macOS) or `bwrap` (Linux) and
cannot **write** outside the workspace, `$TMPDIR` and the declared `runtime.sandboxWritePaths`; on a
host without either tool `IMPLEMENTATION` is blocked instead (#34). It does not restrict reads,
network access or process execution, and validators and the simulated provider are not wrapped.
See [agent sandbox](docs/reference/configuration.md#agent-sandbox).

## Repository supply chain

- GitHub Actions are pinned by commit SHA and run with least-privilege tokens; workflows are
  checked by actionlint and zizmor.
- Secret scanning with push protection, gitleaks over the full history, CodeQL, Bandit, pip-audit,
  Trivy (container image), dependency review for pull requests, Dependabot and OpenSSF Scorecard.
- Releases from `v0.8.1` are built by CI with an SPDX SBOM and a build-provenance attestation:
  `gh attestation verify <file> --repo SebasBarrera/harness-engineering`.

## Security model of the harness

### Trust assumptions

The implementation distinguishes authorization logic from strong containment.

| Element | Trust treatment |
|---|---|
| Released core and built-in primitive adapters | Trusted computing base for the local prototype |
| Locked organizational policy | Trusted only after independent distribution/integrity controls |
| Project configuration | Validated input; cannot weaken locked policies |
| Repository files, names and documentation | Untrusted data and possible prompt-injection content |
| External plugins and native tools | Untrusted processes constrained by declared capabilities, but not strongly sandboxed by the local runner |
| Agent/model output | Untrusted proposal; schema and independent evidence required |

### Enforced in the research beta

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
- With `runtime.agentSandbox: enforce`, command-provider processes cannot write outside the
  workspace, `$TMPDIR` and the declared write paths (`sandbox-exec` on macOS, `bwrap` on Linux);
  without a mechanism the provider is not started. The profile digest and the allowed paths are
  recorded as `IMPLEMENTATION` evidence.

### Not enforced by the local process runner

The local runner is **not an OS sandbox**. Once a native process is launched, capability metadata alone cannot technically prevent that process from:

- accessing files permitted by the operating-system user but outside the workspace (for an agent
  provider under `agentSandbox: enforce`, reading them; writes are denied);
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

### Approval and exception safety

`APPROVE` is rejected when the gate itself failed because of blocking findings. `APPROVE_EXCEPTION` is a distinct, auditable decision and still requires the exact current digest and rationale. Exceptions do not rewrite policy or alter historical evidence.

The current local decision API has no authentication. It is designed only for loopback use during research. Production adoption requires authenticated identity, authorization, CSRF protection, audit retention and transport security.

### Secret handling

The artifact redactor recognizes common authorization headers, secret assignments, GitHub-style tokens, AWS access keys and private-key blocks. This is defense in depth, not proof that all sensitive values are recognized. Callers may pass explicit secret literals for redaction.

Persist prompt/model content only when a reviewed retention policy explicitly permits it. The default design favors prompt/context digests and provider-reported usage metadata.

### Security test inventory

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

### Responsible use

Use synthetic repositories and credentials in demonstrations. Never run the research beta against confidential code or production secrets without a reviewed isolation and retention design.
