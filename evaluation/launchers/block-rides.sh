#!/usr/bin/env bash
# Block rides (ride-hailing and food delivery, evaluation/rides): every valid (condition, level) cell of
# run_rides.py x EVAL_MODELS x EVAL_REPS, one run at a time, then the kept workspaces scored with the
# hidden suite (rides/rescore.py). The runs get a copy of the code without the hidden suite and the
# reference (copy_code), so the record of a run says "hidden: pending" until rescore.py fills it.
# EVAL_RIDES_CELLS restricts the cells ("condition:level" words). The default is the campaign's rides
# block (decision of 2026-11-12): one_line and super, direct against harness; with EVAL_MODELS
# defaulting to Sonnet and EVAL_REPS to 2 here. Any cell of RIDES_VALID may be named.
# EVAL_RIDES_PLAN=1 only prints the runs that would be scheduled (no account check, no run).
# No condition of rides has a turn cap (amendment of 2026-11-12): run_rides.py ignores EVAL_MAX_TURNS.
# EVAL_DRY=1 runs the cells with fake_claude.py (run_rides.py --dry-run, no model call).
# See common.sh for the environment. Usage: EVAL_PYTHON=... EVAL_WORK=... EVAL_CACHE=... EVAL_WHEEL=... block-rides.sh
EVAL_MODELS="${EVAL_MODELS:-claude-sonnet-5-5}"
EVAL_REPS="${EVAL_REPS:-2}"
source "$(dirname "$0")/common.sh"
source "$(dirname "$0")/smoke.sh"
# run_rides.VALID, in the order the block runs them.
RIDES_VALID=(direct:one_line direct:paragraph direct:requirements direct:super stepwise:super harness:one_line
  harness:paragraph harness:requirements harness:super clarify:one_line clarify:paragraph)
RIDES_CAMPAIGN=(direct:one_line harness:one_line direct:super harness:super)
read -r -a CELLS <<< "${EVAL_RIDES_CELLS:-${RIDES_CAMPAIGN[*]}}"
for CELL in "${CELLS[@]}"; do
  case " ${RIDES_VALID[*]} " in
    *" $CELL "*) ;;
    *)
      echo "ABORT: $CELL is not a valid rides cell (valid: ${RIDES_VALID[*]})" >&2
      exit 2
      ;;
  esac
done
if [ "${EVAL_RIDES_PLAN:-0}" = "1" ]; then
  N=0
  for MODEL in $EVAL_MODELS; do
    for CELL in "${CELLS[@]}"; do
      for REP in $(seq 1 "$EVAL_REPS"); do
        echo "$MODEL ${CELL%%:*} ${CELL##*:} r$REP"
        N=$((N + 1))
      done
    done
  done
  echo "$N runs"
  exit 0
fi
keep_awake "$0" "$@"
EVAL_OUT="${EVAL_OUT:-$EVAL_DIR/results-2.0.0/rides}"
mkdir -p "$EVAL_OUT"
check_account
take_lock block-rides
copy_code
record_environment rides
[ -n "${EVAL_MAX_TURNS:-}" ] && log "EVAL_MAX_TURNS=$EVAL_MAX_TURNS ignored: rides has no turn cap in any condition"
run_smoke rides r1
SLOT=$(make_slot r1)
OUT="$EVAL_OUT/rides.jsonl"

# True when the run (model, condition, level, rep) already has a record in $OUT.
recorded() {
  [ -f "$OUT" ] && "$SLOT/$VENV_BIN/python" - "$OUT" "$@" << 'PY'
import json, sys
model, condition, level, rep = sys.argv[2:]
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
sys.exit(0 if any((r["model"], r["condition"], r["level"], str(r["rep"])) == (model, condition, level, rep)
                  for r in rows) else 1)
PY
}

for MODEL in $EVAL_MODELS; do
  for CELL in "${CELLS[@]}"; do
    CONDITION="${CELL%%:*}"
    LEVEL="${CELL##*:}"
    for REP in $(seq 1 "$EVAL_REPS"); do
      RUN="$MODEL $CONDITION $LEVEL r$REP"
      if recorded "$MODEL" "$CONDITION" "$LEVEL" "$REP"; then
        log "skip $RUN (already recorded)"
        continue
      fi
      for ATTEMPT in $(seq 1 "${EVAL_LIMIT_MAX_WAITS:-48}"); do
        log "start $RUN (attempt $ATTEMPT)"
        # A run whose runner died is resumed by the same command (run_rides.py reuses its directory).
        PATH="$(slot_path "$SLOT")" "$SLOT/$VENV_BIN/python" "$CODE/rides/run_rides.py" --level "$LEVEL" \
          --condition "$CONDITION" --model "$MODEL" --rep "$REP" --work "$EVAL_WORK/runs/rides/$MODEL" \
          --out "$OUT" ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} >> "$EVAL_OUT/log-$MODEL.txt" 2>&1
        CODE_RUN=$?
        if [ "$CODE_RUN" = "75" ]; then
          log "usage limit on $RUN: wait ${EVAL_LIMIT_WAIT:-900} s"
          sleep "${EVAL_LIMIT_WAIT:-900}"
          continue
        fi
        if [ "$CODE_RUN" = "0" ]; then log "done $RUN"; else log "FAILED $RUN (exit $CODE_RUN)"; fi
        break
      done
    done
  done
done
# The hidden suite lives only in this checkout: score the kept workspaces with it.
# rescore.py writes rides.scored.jsonl next to rides.jsonl; its per-run lines go to summary-rescore.txt.
if [ -f "$OUT" ] && ! PATH="$(slot_path "$SLOT")" "$SLOT/$VENV_BIN/python" "$EVAL_DIR/rides/rescore.py" "$OUT" \
  > "$EVAL_OUT/summary-rescore.txt" 2>&1; then
  log "FAILED rescore (see summary-rescore.txt)"
  exit 1
fi
finish_environment rides
log "BLOCK-RIDES-DONE"
