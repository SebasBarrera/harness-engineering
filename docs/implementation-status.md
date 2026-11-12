# Implementation status

Snapshot of the 0.8.0 research beta, the cut the thesis documents (tag `v0.8.0`). It is kept as
that cut's record and is not updated with later releases.

> [!NOTE]
> The current version is 2.0.1. Several rows below have changed since 0.8.0: built-in adapters
> for the Claude Code, Codex, Gemini CLI and aider CLIs (`agentProviders.<id>.kind`), an MCP
> server for embedded mode (`harness mcp serve`), token authentication and roles in the local API
> (#18), retention applied by `harness gc`, the closed `thesis-impact` defects (#3–#8) and 80 CLI
> commands. See the [README](https://github.com/SebasBarrera/harness-engineering#readme), the
> [CLI reference](reference/cli.md), the
> [changelog](https://github.com/SebasBarrera/harness-engineering/blob/develop/CHANGELOG.md) and the
> [trust model](architecture/trust-model.md).

## Complete in this repository

| Area | Status | Notes |
|---|---|---|
| Domain model | Complete for current MVP | Typed entities and normalized states for task, run, phases, changes, evidence, findings, decisions, invocations, memory and retrospective |
| Normative workflow | Complete | Nine required phases plus separate retrospective |
| State machine | Complete | Strict order, correction path, invalidation, cancellation and resumption |
| Configuration snapshots | Complete | Project/profile/workflow/policy digests stored per execution |
| Event store | Complete local implementation | SQLite WAL, append-only API, hash chain, JSONL export |
| Artifact store | Complete local implementation | Content addressed, atomic, redacted and digest verified |
| ChangeSet | Complete for structured tasks | Baseline, task-owned paths, deterministic digest, unrelated-change exclusion |
| Capabilities | Complete application-level model | Actor, scope, expiration, deny-by-default and command authorization |
| Process runner | Complete local adapter | No shell, timeout, cancellation, output limits; not a strong sandbox |
| Agent provider 1 | Complete | Deterministic simulated provider |
| Agent provider 2 | Complete generic adapter | Structured external command/JSON provider |
| Validators | Complete foundational set | Generic command validator plus independent deterministic review |
| Technology profiles | Complete initial proof | Python and Node.js |
| Gates | Complete | Fail closed, typed severities, mandatory-check semantics |
| Human decisions | Complete | Approve, reject, changes requested and explicit exception; exact digest binding |
| Revalidation | Complete | Manual correction + continue; stale approvals rejected |
| Memory | Complete deterministic MVP | Five levels, provenance, approval, expiry and scoped selection |
| Telemetry | Complete basic projection | Timings, invocations, retries, validation cycles, findings, changes; no invented tokens/cost |
| Retrospective | Complete deterministic MVP | Evidence-based observations and recommendations; never auto-applied |
| CLI | Complete MVP | Initialization, tasks, runs, decisions, status, evidence, findings, trace, doctor, benchmarks and API |
| API/web | Complete local MVP | Read status/trace/findings/evidence/retrospective and issue digest-bound decisions |
| Reporting | Complete MVP | Markdown, JSON, JSONL and SARIF |
| Plugin protocol | Complete v1 prototype | One-shot external JSON request/response, client validation and SDK echo server |
| Packaging | Complete | Wheel, source distribution, Dockerfile and CI definition |
| Tests | Complete for declared beta scope | Unit, contract, integration, security, E2E and performance smoke tests |
| Benchmarks | Complete reproducible baseline | Microbenchmarks and direct-vs-governed synthetic Python/Node scenarios |

## Partial or intentionally constrained

| Area | Current implementation | Remaining work for production |
|---|---|---|
| Sandboxing | Application-level capabilities, path checks, timeout/cancellation; write confinement of agent providers (`runtime.agentSandbox`: `sandbox-exec` on macOS, `bwrap` on Linux) | Container/VM/OS sandbox for validators and plugins; read, network, CPU/memory/PID controls |
| Git | Read-only baseline metadata and workspace diff | Stage/commit/push adapters, ownership by hunk and signed attestations |
| Coverage | Can run profile commands; whole-project tools can be configured | Normalized diff-coverage adapter per stack |
| SAST/SCA | External command/plugin contract supports them | Built-in parsers and curated default policies |
| DAST | Representable as validator/plugin | Lifecycle, target isolation, probes and generic normalization |
| Real model providers | Generic command adapter | Provider-specific session, resume, event and token APIs |
| Usage/cost | Records provider-reported values when available | Provider-specific collection and pricing snapshots |
| Multi-unit projects | Domain/configuration structures exist | Impact graph and parallel scheduler across units |
| Plugins | Protocol and capability-aware launcher | Persistent lifecycle, registry, signature verification and isolation |
| Web | Functional local dashboard | Authentication, multiuser authorization, accessibility hardening and richer evidence views |
| Retention | Configuration fields and local stores | Automated retention, encryption and legal hold |
| Replay | Trace and snapshots are available | Environment/plugin lock reconstruction and comparative replay command |
| Attestation | Digests and hash chain | External signature/OIDC/in-toto compatible attestation |

## Explicitly deferred

- GraphRAG and semantic graph memory.
- Automatic workflow generation.
- Automatic changes to phases, gates, permissions, prompts or normative memory.
- Distributed execution and remote workers.
- Multi-database or multi-tenant platform.
- Plugin marketplace.
- Generic production DAST.
- Broad language catalog beyond Python and Node.js.
- Production MCP server lifecycle and transport.

These items are deferred because they are not necessary to demonstrate the core research hypothesis and would materially increase security and evaluation complexity.
