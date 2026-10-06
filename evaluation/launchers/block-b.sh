#!/usr/bin/env bash
# Block B (P11 + N09 + N11): instruction quality. The casual and the minimal ("It works.") prompts
# x {direct, harness-core, harness} x models x scenarios x EVAL_REPS repetitions. The harness
# conditions answer INTENT's questions through the simulated product owner. See common.sh for the
# environment. Usage: EVAL_PYTHON=... EVAL_WORK=... EVAL_CACHE=... EVAL_WHEEL=... block-b.sh
source "$(dirname "$0")/common.sh"
keep_awake "$0" "$@"
EVAL_OUT="${EVAL_OUT:-$EVAL_DIR/results-2.0.0/block-b}"
mkdir -p "$EVAL_OUT"
check_account
take_lock block-b
copy_code
record_environment B
CONDITIONS="${EVAL_CONDITIONS:-direct,harness-core,harness}"
for MODEL in $EVAL_MODELS; do
  for PROMPT in ${EVAL_PROMPTS:-casual poor}; do
    log "model $MODEL prompt $PROMPT"
    PIDS=()
    i=0
    for SC in $EVAL_SCENARIOS; do
      i=$((i + 1))
      SLOT=$(make_slot "b$i")
      CMD=("$SLOT/bin/python" "$CODE/run_matrix.py" --model "$MODEL" --reps "$EVAL_REPS" --scenarios "$SC"
        --prompt "$PROMPT" --conditions "$CONDITIONS" --work "$EVAL_WORK/runs/block-b/$MODEL"
        --cache "$EVAL_CACHE" --out "$EVAL_OUT/B-$PROMPT-$MODEL-$SC.jsonl" ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} ${CORE_FLAG[@]+"${CORE_FLAG[@]}"})
      if [ "$EVAL_PARALLEL" = "1" ]; then
        PATH="$(slot_path "$SLOT")" "${CMD[@]}" >> "$EVAL_OUT/log-$MODEL-$PROMPT-$SC.txt" 2>&1 &
        PIDS+=($!)
      else
        PATH="$(slot_path "$SLOT")" "${CMD[@]}" >> "$EVAL_OUT/log-$MODEL-$PROMPT-$SC.txt" 2>&1
      fi
    done
    [ ${#PIDS[@]} -gt 0 ] && wait ${PIDS[@]+"${PIDS[@]}"}
  done
done
SLOT=$(make_slot b1)
"$SLOT/bin/python" "$CODE/report.py" --v2 "$EVAL_OUT" "$EVAL_OUT/summary-blocks.json"
"$SLOT/bin/python" "$CODE/report_prompts.py" --v2 "$EVAL_OUT" "$EVAL_OUT/summary-prompts.json" > /dev/null
log "BLOCK-B-DONE"
