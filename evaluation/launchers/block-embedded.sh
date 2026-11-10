#!/usr/bin/env bash
# Embedded block (N08): Claude Code as the host session drives the harness over MCP and the skill;
# the host model is the factor. EVAL_EMBEDDED_SCENARIO (default brownfield) x models x
# EVAL_EMBEDDED_REPS (default 2), one run at a time. EVAL_EMBEDDED_VARIANT: init (default, the
# harness condition's configuration) or no-acceptance (see evaluation/README.md, "Embedded mode").
# See common.sh for the environment. Usage: EVAL_PYTHON=... EVAL_WORK=... EVAL_CACHE=... EVAL_WHEEL=... block-embedded.sh
source "$(dirname "$0")/common.sh"
keep_awake "$0" "$@"
EVAL_OUT="${EVAL_OUT:-$EVAL_DIR/results-2.0.0/block-embedded}"
mkdir -p "$EVAL_OUT"
check_account
take_lock block-embedded
copy_code
record_environment embedded
SC="${EVAL_EMBEDDED_SCENARIO:-brownfield}"
VARIANT="${EVAL_EMBEDDED_VARIANT:-init}"
SLOT=$(make_slot e1)
for MODEL in $EVAL_MODELS; do
  for REP in $(seq 1 "${EVAL_EMBEDDED_REPS:-2}"); do
    if [ -f "$EVAL_OUT/embedded-$VARIANT-$MODEL.jsonl" ] && grep -q "\"rep\": $REP," "$EVAL_OUT/embedded-$VARIANT-$MODEL.jsonl"; then
      continue
    fi
    log "host $MODEL rep $REP ($SC, $VARIANT)"
    PATH="$(slot_path "$SLOT")" "$SLOT/bin/python" "$CODE/run_embedded.py" --scenario "$SC" --model "$MODEL" \
      --rep "$REP" --variant "$VARIANT" --work "$EVAL_WORK/runs/block-embedded/$MODEL" --cache "$EVAL_CACHE" \
      --out "$EVAL_OUT/embedded-$VARIANT-$MODEL.jsonl" ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} \
      >> "$EVAL_OUT/log-$MODEL.txt" 2>&1 || log "FAILED host $MODEL rep $REP"
  done
done
log "BLOCK-EMBEDDED-DONE"
