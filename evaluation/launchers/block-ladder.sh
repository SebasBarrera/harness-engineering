#!/usr/bin/env bash
# Block ladder (T6, cost ladder): brownfield, full prompt, direct -> harness-core -> harness-nopanel
# -> harness, EVAL_LADDER_REPS repetitions (default 3), model EVAL_LADDER_MODEL (default Sonnet).
# See common.sh for the environment. Usage: EVAL_PYTHON=... EVAL_WORK=... EVAL_CACHE=... EVAL_WHEEL=... block-ladder.sh
source "$(dirname "$0")/common.sh"
keep_awake "$0" "$@"
EVAL_OUT="${EVAL_OUT:-$EVAL_DIR/results-2.0.0/block-ladder}"
mkdir -p "$EVAL_OUT"
check_account
take_lock block-ladder
copy_code
record_environment LADDER
MODEL="${EVAL_LADDER_MODEL:-claude-sonnet-5-5}"
REPS="${EVAL_LADDER_REPS:-3}"
log "model $MODEL ladder (direct,harness-core,harness-nopanel,harness x $REPS)"
SLOT=$(make_slot l1)
CMD=("$SLOT/$VENV_BIN/python" "$CODE/run_matrix.py" --model "$MODEL" --reps "$REPS" --scenarios brownfield
  --prompt full --conditions direct,harness-core,harness-nopanel,harness --work "$EVAL_WORK/runs/block-ladder/$MODEL"
  --cache "$EVAL_CACHE" --out "$EVAL_OUT/LADDER-$MODEL.jsonl" ${CORE_FLAG[@]+"${CORE_FLAG[@]}"} ${DRY_FLAG[@]+"${DRY_FLAG[@]}"})
PATH="$(slot_path "$SLOT")" "${CMD[@]}" >> "$EVAL_OUT/log-$MODEL.txt" 2>&1
"$SLOT/$VENV_BIN/python" "$CODE/report.py" --v2 "$EVAL_OUT" "$EVAL_OUT/summary-blocks.json"
log "BLOCK-LADDER-DONE"
