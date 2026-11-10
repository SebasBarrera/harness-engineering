#!/usr/bin/env bash
# Block D (P13 + P20 + N01-b): iterative development, five increments of one library.
# {casual direct (baseline), structured direct (structured), harness} x models x EVAL_REPS sessions;
# the three conditions of a model and repetition run at the same time, one slot venv each.
# See common.sh for the environment. Usage: EVAL_PYTHON=... EVAL_WORK=... EVAL_CACHE=... EVAL_WHEEL=... block-d.sh
source "$(dirname "$0")/common.sh"
keep_awake "$0" "$@"
EVAL_OUT="${EVAL_OUT:-$EVAL_DIR/results-2.0.0/block-d}"
mkdir -p "$EVAL_OUT"
check_account
take_lock block-d
copy_code
record_environment D
done_session() {  # condition model rep -> 0 when the output already has that session
  [ -f "$EVAL_OUT/longitudinal-$2.jsonl" ] && "$EVAL_PYTHON" - "$EVAL_OUT/longitudinal-$2.jsonl" "$1" "$3" << 'PY'
import json, sys
path, condition, rep = sys.argv[1], sys.argv[2], int(sys.argv[3])
rows = [json.loads(line) for line in open(path) if line.strip()]
sys.exit(0 if any(r["condition"] == condition and r["rep"] == rep for r in rows) else 1)
PY
}
for MODEL in $EVAL_MODELS; do
  for REP in $(seq 1 "$EVAL_REPS"); do
    log "model $MODEL session $REP"
    PIDS=()
    i=0
    for CONDITION in ${EVAL_D_CONDITIONS:-baseline structured harness}; do
      i=$((i + 1))
      if done_session "$CONDITION" "$MODEL" "$REP"; then continue; fi
      SLOT=$(make_slot "d$i")
      CMD=("$SLOT/bin/python" "$CODE/longitudinal/run_session.py" --condition "$CONDITION" --model "$MODEL"
        --rep "$REP" --work "$EVAL_WORK/runs/block-d/$MODEL" --out "$EVAL_OUT/longitudinal-$MODEL-$CONDITION.part.jsonl"
        ${DRY_FLAG[@]+"${DRY_FLAG[@]}"})
      if [ "$EVAL_PARALLEL" = "1" ]; then
        PATH="$(slot_path "$SLOT")" "${CMD[@]}" >> "$EVAL_OUT/log-$MODEL-$CONDITION.txt" 2>&1 &
        PIDS+=($!)
      else
        PATH="$(slot_path "$SLOT")" "${CMD[@]}" >> "$EVAL_OUT/log-$MODEL-$CONDITION.txt" 2>&1
      fi
    done
    [ ${#PIDS[@]} -gt 0 ] && wait ${PIDS[@]+"${PIDS[@]}"}
    # Parallel sessions write their own part files; the model's file collects them in order.
    for CONDITION in ${EVAL_D_CONDITIONS:-baseline structured harness}; do
      PART="$EVAL_OUT/longitudinal-$MODEL-$CONDITION.part.jsonl"
      [ -f "$PART" ] && cat "$PART" >> "$EVAL_OUT/longitudinal-$MODEL.jsonl" && rm -f "$PART"
    done
  done
done
SLOT=$(make_slot d1)
"$SLOT/bin/python" "$CODE/longitudinal/report.py" "$EVAL_OUT" "$EVAL_OUT/summary-longitudinal.json" > /dev/null
log "BLOCK-D-DONE"
