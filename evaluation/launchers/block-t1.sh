#!/usr/bin/env bash
# Block T1 (value of asking): the same poor (casual) prompt, the same model, four conditions:
#   direct                 casual prompt, Claude Code alone
#   harness                casual prompt, harness with the simulated product owner
#   harness without PO     casual prompt, harness, the person answers "decide yourself" and the harness
#                          records assumptions (run_matrix.py --no-product-owner)
#   direct, full prompt    the ceiling: direct with the complete prompt
# Models: Haiku and Sonnet; repetitions per model: EVAL_T1_REPS_SONNET (default 5), EVAL_T1_REPS_HAIKU
# (default 3). See common.sh for the environment. Usage: EVAL_PYTHON=... EVAL_WORK=... EVAL_CACHE=... EVAL_WHEEL=... block-t1.sh
source "$(dirname "$0")/common.sh"
keep_awake "$0" "$@"
EVAL_OUT="${EVAL_OUT:-$EVAL_DIR/results-2.0.0/block-t1}"
mkdir -p "$EVAL_OUT"
check_account
take_lock block-t1
copy_code
record_environment T1
T1_MODELS="${EVAL_T1_MODELS:-claude-haiku-4-5-20251001 claude-sonnet-5-5}"
reps_of() {  # model -> repetitions
  case "$1" in
    *haiku*) echo "${EVAL_T1_REPS_HAIKU:-3}" ;;
    *sonnet*) echo "${EVAL_T1_REPS_SONNET:-5}" ;;
    *) echo "${EVAL_REPS}" ;;
  esac
}
run_part() {  # tag prompt conditions extra-flag
  local tag="$1" prompt="$2" conditions="$3" extra="$4"
  for MODEL in $T1_MODELS; do
    local reps
    reps=$(reps_of "$MODEL")
    log "model $MODEL $tag ($conditions x $reps, prompt $prompt)"
    PIDS=()
    i=0
    for SC in $EVAL_SCENARIOS; do
      i=$((i + 1))
      SLOT=$(make_slot "t$i")
      CMD=("$SLOT/$VENV_BIN/python" "$CODE/run_matrix.py" --model "$MODEL" --reps "$reps" --scenarios "$SC"
        --prompt "$prompt" --conditions "$conditions" --work "$EVAL_WORK/runs/block-t1/$MODEL"
        --cache "$EVAL_CACHE" --out "$EVAL_OUT/T1-$tag-$MODEL-$SC.jsonl" ${extra:+$extra} ${DRY_FLAG[@]+"${DRY_FLAG[@]}"})
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
run_part casual casual direct,harness ""
run_part nopo casual harness --no-product-owner
run_part ceiling full direct ""
SLOT=$(make_slot t1)
"$SLOT/$VENV_BIN/python" "$CODE/report.py" --v2 "$EVAL_OUT" "$EVAL_OUT/summary-blocks.json"
log "BLOCK-T1-DONE"
