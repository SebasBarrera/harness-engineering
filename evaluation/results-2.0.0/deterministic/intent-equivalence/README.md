# P24: INTENT equivalence without agents

Every task the experiments sent (or would send) to the harness, assessed with the deterministic
INTENT rules of the harness the script runs with (`assess_intent`: C0, C1, C2, C3, T1). A task
file is loaded as `harness task create` loads it under `intake.criteriaPolicy: enforce`
(`load_task_file`). A free-text prompt (the one-line prompts, the casual increments and the casual
bug-report fixes) is assessed twice: as `harness do TEXT` builds the task (`task_from_text`: the
text is the intent and the criterion) and as a task file whose only content is the text as intent
(no criteria: rule C0). The rules that need an agent (A1, A3) or the workspace and the answers
(A2, P1) are not applied. No model was called.

Sources: `evaluation/tasks` (3 full, 3 poor, 3 casual), the 5 increments of
`evaluation/longitudinal/increments.yaml` (structured task and casual prompt), the 16 bug reports
of `evaluation/longitudinal/bug_reports.yaml` (the single-report fix task `run_session.py` builds
and its casual prompt), and the whole-project and per-step tasks of `evaluation/rides` and
`evaluation/large` (built by their runners' `whole_task` and `step_task`).

Command (worktree code, 2026-10-06):

```bash
.venv/bin/python evaluation/deterministic/intent_equivalence.py --harness "$PWD/.venv/bin/harness" \
  --out evaluation/results-2.0.0/deterministic/intent-equivalence
```

Files: `intent-equivalence.jsonl` (one record per task and interpretation: criteria,
requirements, questions and questions per rule), `intent-equivalence-summary.json` (per source),
`environment.json`.
