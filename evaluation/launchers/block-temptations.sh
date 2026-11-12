#!/usr/bin/env bash
# Block T2 (temptations): the six tasks of evaluation/temptations, where the easy path is the
# defect, x {direct, harness} x EVAL_T2_REPS repetitions (default 5), model EVAL_T2_MODEL (default
# Sonnet). The harness condition answers INTENT's questions through the simulated product owner
# (it knows the task and the fixture's README, not the defect); EVAL_T2_NO_PO=1 runs it without
# product owner instead (harness-nopo). See common.sh for the environment (the Windows path guard
# and EVAL_DRY included). Usage: EVAL_PYTHON=... EVAL_WORK=... EVAL_CACHE=... EVAL_WHEEL=... block-temptations.sh
#
# Before the first real call, smoke.sh runs one dry cell per condition (block T2, one task) and
# stops the block with exit code 6 if a condition leaves no valid record.
# run_temptations.py does not skip the runs already in its output file, so a block that stopped is
# started again with a new EVAL_OUT, not resumed.
source "$(dirname "$0")/common.sh"
source "$(dirname "$0")/smoke.sh"
keep_awake "$0" "$@"
EVAL_OUT="${EVAL_OUT:-$EVAL_DIR/results-2.0.0/block-temptations}"
mkdir -p "$EVAL_OUT"
check_account
take_lock block-temptations
copy_code
record_environment T2
MODEL="${EVAL_T2_MODEL:-claude-sonnet-5-5}"
EVAL_SMOKE_MODEL="${EVAL_SMOKE_MODEL:-$MODEL}" run_smoke T2 t2
REPS="${EVAL_T2_REPS:-5}"
NO_PO_FLAG=()
[ "${EVAL_T2_NO_PO:-0}" = "1" ] && NO_PO_FLAG=(--no-product-owner)
log "model $MODEL temptations (direct,harness x $REPS)${NO_PO_FLAG[*]:+ without product owner}"
SLOT=$(make_slot t2)
CMD=("$SLOT/$VENV_BIN/python" "$CODE/temptations/run_temptations.py" --model "$MODEL" --reps "$REPS"
  --conditions direct harness --work "$EVAL_WORK/runs/block-temptations/$MODEL"
  --out "$EVAL_OUT/T2-$MODEL.jsonl" ${NO_PO_FLAG[@]+"${NO_PO_FLAG[@]}"} ${DRY_FLAG[@]+"${DRY_FLAG[@]}"})
PATH="$(slot_path "$SLOT")" "${CMD[@]}" >> "$EVAL_OUT/log-$MODEL.txt" 2>&1
finish_environment T2
log "BLOCK-T2-DONE"
