# Metrics

Two different families of metrics appear in this repository. Keep them apart:

1. **Harness telemetry**: process metrics that the harness computes for every governed run (this
   page). They describe what a run did and what evidence it produced.
2. **Repository health**: CI results, coverage, static analysis, benchmarks and supply-chain
   signals about this code base. See [monitoring](monitoring.md) and [benchmarks](benchmarks.md).

## Principles

- **Per run, never per person.** The harness produces no individual indicators. Actor identifiers
  are recorded for traceability of decisions, but no metric aggregates by actor. During the design
  research, participants anticipated changing their behavior if an individual indicator fed a
  performance evaluation; the design responds by not computing one (requirement RD-16 of the
  thesis). `scripts/metrics_report.py` keeps that property.
- **Every value declares its quality.** A metric is `OBSERVED` (counted from persisted records or
  events), `DERIVED` (computed from observed values), `REPORTED` (supplied by a provider),
  `ESTIMATED` or `NOT_AVAILABLE`. `ESTIMATED` exists in the model but no metric currently uses it.
- **Nothing is invented.** Token and cost values are `NOT_AVAILABLE` unless a provider reports them;
  cost is never inferred from tokens.

## Catalogue

The list below is the complete set computed by `MetricsProjector.project` in
`src/governed_harness/telemetry/metrics.py`.

| Key | Unit | Quality | Definition (as implemented) | Source | Limitations |
|---|---|---|---|---|---|
| `duration.total_ms` | ms | OBSERVED | Wall-clock time between the first and last persisted execution event. | event store | Includes human waiting time. |
| `duration.phase_ms` | ms | DERIVED | Sum of completed phase wall-clock durations; parallel phases would be double-counted. | phase records | Not equal to total wall time when phases overlap. |
| `duration.agent_ms` | ms | DERIVED | Sum of persisted agent invocation durations. | agent invocation records | |
| `duration.tool_ms` | ms | DERIVED | Sum of persisted tool invocation durations. | tool invocation records | |
| `duration.human_wait_ms` | ms | DERIVED, or NOT_AVAILABLE | Elapsed time from the latest gate evaluation to the latest human decision. | gate and decision events | NOT_AVAILABLE until a decision follows a gate evaluation. |
| `agent.invocations` | count | OBSERVED | Number of persisted agent invocation records. | agent invocation records | |
| `agent.transient_retries` | count | OBSERVED | Command-provider calls repeated after a transient failure (`runtime.providerRetries`, `agent.invocation.retried` events). | event store | The failed calls are also counted in `agent.invocations`. |
| `agent.unsupported_claims` | count | DERIVED | Findings `agent.unsupported-claim`: the agent reported success and the verification of its change failed. | finding records | Recorded only when `runtime.verificationCorrections` is set and the provider is a command provider. |
| `tool.invocations` | count | OBSERVED | Number of persisted tool invocation records. | tool invocation records | |
| `implementation.attempts` | count | DERIVED | Count of `IMPLEMENTATION` phase-start events. | event store | |
| `correction.cycles` | count | OBSERVED | Count of authorized transitions back to `IMPLEMENTATION`: `REQUEST_CHANGES` decisions and automatic corrections after a failed `VERIFICATION` (`correction.authorized` events). | event store | Before the automatic corrections existed it counted `REQUEST_CHANGES` only. |
| `correction.verification_cycles` | count | OBSERVED | Automatic corrections after a failed `VERIFICATION` (`runtime.verificationCorrections`). | event store | 0 when the setting is absent or the provider is simulated. |
| `review.cycles` | count | DERIVED | Count of `INDEPENDENT_REVIEW` phase-start events. | event store | |
| `replanning.count` | count | OBSERVED | Count of plan replacement events after the first accepted plan. | event store | No component emits the `plan.replaced` event yet, so the value is always 0. |
| `validation.non_passed` | count | DERIVED | Validation results whose normalized status is not `PASSED` (includes `NOT_APPLICABLE`). | validation records | Counts optional validators that did not apply. |
| `changesets.count` | count | OBSERVED | Number of distinct persisted ChangeSet records. | ChangeSet records | |
| `changeset.files` | count | DERIVED | Unique paths appearing in persisted ChangeSets. | ChangeSet records | |
| `human.decisions` | count | OBSERVED | Number of persisted human decisions. | decision records | |
| `human.interactions` | count | OBSERVED | Events a person caused on the run: decisions, clarification answers, plan, acceptance and preflight decisions, budget raises, quarantines, contract confirmations (one confirmed with the clarification answers, or with a pre-authorised approval, counts with them), attached evidence (since #55; reported against `intake.interruptions.target`), approvals given in advance and plan approvals (since #58). A decision the harness records from a pre-authorisation is not counted again. | run events with a human actor | |
| `tokens.input` | tokens | REPORTED, or NOT_AVAILABLE | Sum of provider-reported input tokens; no estimation. | resource usage records | See below. |
| `tokens.output` | tokens | REPORTED, or NOT_AVAILABLE | Sum of provider-reported output tokens; no estimation. | resource usage records | See below. |
| `tokens.reasoning` | tokens | REPORTED, or NOT_AVAILABLE | Sum of provider-reported reasoning tokens; no estimation. | resource usage records | See below. |
| `cost.usd` | USD | REPORTED, or NOT_AVAILABLE | Sum of provider-reported costs; never inferred from tokens. | resource usage records | See below. |

**When tokens and cost are `NOT_AVAILABLE`.** They are read from `ResourceUsage` records. The
simulated provider does not call a model and creates none. A command provider creates one when its
response carries the optional `usage` object (see
[connecting an external agent](guides/external-agents.md)); the values are then `REPORTED`. When a
provider reports nothing, the harness shows the gap instead of estimating it.

## Aggregated metrics

`harness metrics` (since 2.0, issue #58) aggregates these records over the runs of a repository,
or of every repository of the run registry, into tokens and cost by agent, model, task and phase,
lines, delivery, time, quality, friction against per-size targets and trends, with zero model
calls; costs of providers that report tokens without a cost are estimated from a price table and
labelled `estimated`. See [local metrics](guides/local-metrics.md).

## Reading the metrics

| Where | How |
|---|---|
| CLI | `harness status --path P --run R` (`metrics` object, one entry per key with value, unit, quality, definition, source and limitations) |
| Trace | `harness trace --run R --format json` (also `jsonl`, `markdown`, `sarif` for findings) |
| Retrospective | `harness retrospect --run R` derives observations from these metrics; nothing is applied (`appliedAutomatically: false`) |
| API | `GET /api/runs/{run}` returns the same status projection as the CLI |

Example from the later-change flow (`scripts/demo_flows.py later-change`): `implementation.attempts`
2, `correction.cycles` 1, `changesets.count` 3, `human.decisions` 2.

## HTML report

`scripts/metrics_report.py` reads one or more governed projects through the application layer (no
SQL) and renders a self-contained HTML page: gate and run states, median duration per phase,
attempts and cycles per run, human wait, authorized exceptions with their rationale, and the quality
of every metric. It aggregates per run and per project only.

```bash
python scripts/metrics_report.py path/to/project [more projects] --output metrics.html --json metrics.json
```

The documentation site publishes a sample report generated from the demonstration flows
(quickstart, later change, broken baseline, review exception, Node.js) with the simulated
provider.
