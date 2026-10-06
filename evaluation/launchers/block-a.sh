#!/usr/bin/env bash
# Block A (P09 + P10 + N10): the complete task x {direct, harness} x models x scenarios x EVAL_REPS,
# then harness-tiered x the same models as invoking models x scenarios x EVAL_TIERED_REPS (default 2).
# See common.sh for the environment. Usage: EVAL_PYTHON=... EVAL_WORK=... EVAL_CACHE=... EVAL_WHEEL=... block-a.sh
source "$(dirname "$0")/common.sh"
keep_awake "$0" "$@"
EVAL_OUT="${EVAL_OUT:-$EVAL_DIR/results-2.0.0/block-a}"
mkdir -p "$EVAL_OUT"
check_account
take_lock block-a
copy_code
record_environment A
run_part() {  # conditions reps tag
  local conditions="$1" reps="$2" tag="$3"
  for MODEL in $EVAL_MODELS; do
    log "model $MODEL $tag ($conditions x $reps)"
    PIDS=()
    i=0
    for SC in $EVAL_SCENARIOS; do
      i=$((i + 1))
      SLOT=$(make_slot "a$i")
      CMD=("$SLOT/bin/python" "$CODE/run_matrix.py" --model "$MODEL" --reps "$reps" --scenarios "$SC"
        --prompt full --conditions "$conditions" --work "$EVAL_WORK/runs/block-a/$MODEL"
        --cache "$EVAL_CACHE" --out "$EVAL_OUT/A-$tag-$MODEL-$SC.jsonl" ${DRY_FLAG[@]+"${DRY_FLAG[@]}"})
      if [ "$EVAL_PARALLEL" = "1" ]; then
        PATH="$(slot_path "$SLOT")" "${CMD[@]}" >> "$EVAL_OUT/log-$MODEL-$tag-$SC.txt" 2>&1 &
        PIDS+=($!)
      else
        PATH="$(slot_path "$SLOT")" "${CMD[@]}" >> "$EVAL_OUT/log-$MODEL-$tag-$SC.txt" 2>&1
      fi
    done
    [ ${#PIDS[@]} -gt 0 ] && wait ${PIDS[@]+"${PIDS[@]}"}
  done
}
run_part "${EVAL_CONDITIONS:-direct,harness}" "$EVAL_REPS" main
run_part harness-tiered "${EVAL_TIERED_REPS:-2}" tiered
SLOT=$(make_slot a1)
"$SLOT/bin/python" "$CODE/report.py" --v2 "$EVAL_OUT" "$EVAL_OUT/summary-blocks.json"
log "BLOCK-A-DONE"
