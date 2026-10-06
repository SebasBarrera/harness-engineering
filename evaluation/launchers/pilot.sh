#!/usr/bin/env bash
# Pilot of the 2.0.0 evaluation: one scenario, one model, one repetition per condition, one run at a
# time: full prompt x {direct, harness-core, harness, harness-anchored}, then the casual prompt under
# the harness with the simulated product owner (block B). At most five runs.
# EVAL_PILOT_MODEL (default claude-haiku-4-5-20251001), EVAL_PILOT_SCENARIO (default brownfield),
# EVAL_PILOT_RUNS (default: all five, as "prompt:condition" words).
# See common.sh for the environment. Usage: EVAL_PYTHON=... EVAL_WORK=... EVAL_CACHE=... pilot.sh
source "$(dirname "$0")/common.sh"
keep_awake "$0" "$@"
EVAL_OUT="${EVAL_OUT:-$EVAL_DIR/results-2.0.0/pilot}"
mkdir -p "$EVAL_OUT"
check_account
take_lock pilot
copy_code
record_environment pilot
MODEL="${EVAL_PILOT_MODEL:-claude-haiku-4-5-20251001}"
SC="${EVAL_PILOT_SCENARIO:-brownfield}"
SLOT=$(make_slot p1)
for RUN in ${EVAL_PILOT_RUNS:-full:direct full:harness-core full:harness full:harness-anchored casual:harness}; do
  PROMPT="${RUN%%:*}"
  CONDITION="${RUN##*:}"
  if [ -f "$EVAL_OUT/pilot.jsonl" ] && "$SLOT/bin/python" - "$EVAL_OUT/pilot.jsonl" "$PROMPT" "$CONDITION" << 'PY'; then
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
sys.exit(0 if any(r.get("prompt") == sys.argv[2] and r["condition"] == sys.argv[3] for r in rows) else 1)
PY
    log "skip $RUN (already recorded)"
    continue
  fi
  for ATTEMPT in $(seq 1 "${EVAL_LIMIT_MAX_WAITS:-48}"); do
    log "start $RUN $MODEL $SC (attempt $ATTEMPT)"
    PATH="$(slot_path "$SLOT")" "$SLOT/bin/python" "$CODE/run_eval.py" --scenario "$SC" --condition "$CONDITION" \
      --prompt "$PROMPT" --model "$MODEL" --rep 1 --work "$EVAL_WORK/runs/pilot" --cache "$EVAL_CACHE" \
      --out "$EVAL_OUT/pilot.jsonl" ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} ${CORE_FLAG[@]+"${CORE_FLAG[@]}"} \
      >> "$EVAL_OUT/log-runs.txt" 2>&1
    CODE_RUN=$?
    if [ "$CODE_RUN" = "75" ]; then
      # The usage limit: the run is in pilot.invalid.jsonl; wait and run it again.
      log "usage limit on $RUN: wait ${EVAL_LIMIT_WAIT:-900} s"
      sleep "${EVAL_LIMIT_WAIT:-900}"
      continue
    fi
    [ "$CODE_RUN" = "0" ] && log "done $RUN" || log "FAILED $RUN (exit $CODE_RUN)"
    break
  done
done
"$SLOT/bin/python" "$CODE/report.py" --v2 "$EVAL_OUT" "$EVAL_OUT/summary-blocks.json"
log "PILOT-DONE"
