#!/usr/bin/env bash
# Smoke test of a block (sourced by block-*.sh after record_environment; never run on its own).
#
# run_smoke <block> <slot>: with the SAME EVAL_WORK and the slot venv the block is about to use, one
# dry cell per condition of the block (fake_claude.py, no account, no model call, whatever EVAL_DRY
# says), then a check that every condition left a valid record: written to the records file and not
# to <out>.invalid.jsonl, and, in the governed conditions, with a run the harness started (an
# executionId; the record keeps it anonymized as harness.runId "run"). Any failure leaves the block
# with exit code 6 before its first real call. EVAL_SKIP_SMOKE=1 skips it.
#
# Blocks: A, B, T1 and LADDER (run_eval.py cells), D (longitudinal/run_session.py), embedded
# (run_embedded.py). EVAL_SMOKE_MODEL overrides the model of the smoke cells (T1 and LADDER set it). The rides block has no dry run (run_rides.py), so its smoke test is a declared skip.
# The records go to $EVAL_WORK/smoke/<block>, never to the block's results directory.
#
# Depth: the dry cells run in the SAME work directory a real cell of the block uses
# ($EVAL_WORK/runs/<block directory>/<model>), so the state path of a run is as long as in the
# campaign (the Windows MAX_PATH guard in common.sh depends on it). Their repetition is the reserved
# SMOKE_REP (99): "-r99" is as long as the longest real one ("-r10"), and the run directories it
# leaves (named ...-r99-...) are removed before and after, so they never mix with real runs.
SMOKE_REP=99

# smoke_work <block> <model>: the work directory of a real cell of the block, for the smoke cells.
smoke_work() {
  local dir
  case "$1" in
    A) dir=block-a ;;
    B) dir=block-b ;;
    D) dir=block-d ;;
    T1) dir=block-t1 ;;
    LADDER) dir=block-ladder ;;
    *) dir="block-$1" ;;
  esac
  echo "$EVAL_WORK/runs/$dir/$2"
}

# smoke_clean <work>: removes the run directories of the reserved repetition.
smoke_clean() {
  [ -d "$1" ] && find "$1" -mindepth 1 -maxdepth 1 -type d -name "*-r${SMOKE_REP}-*" -exec rm -rf {} +
  return 0
}

# smoke_report_depth <block> <work>: logs the length of the state path of the smoke run (Windows
# form) next to the one of a real cell with the same name length.
smoke_report_depth() {
  local run state
  run=$(find "$2" -mindepth 1 -maxdepth 1 -type d -name "*-r${SMOKE_REP}-*" | head -n 1)
  [ -n "$run" ] || return 0
  state="$run/state"
  [ "$EVAL_WINDOWS" = "1" ] && state="$(cygpath -w "$state")"
  log "smoke $1: state path of a smoke run is ${#state} characters (a real cell of the same name: the same; limit 140)"
}

# check_smoke <slot> <kind> <records.jsonl> <mode>: exit 0 when the record is valid. Mode 0: not
# governed; 1: governed (needs a started run); 2: governed, and the 1.0.0 configuration's refusal of
# a task without criteria (harness-core with the casual prompt, a designed outcome) is valid too.
check_smoke() {
  "$1/$VENV_BIN/python" - "$2" "$3" "$4" << 'PY'
import json, sys
from pathlib import Path

kind, path, mode = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
governed = mode != "0"
invalid = path.with_name(path.stem + ".invalid.jsonl")
if invalid.exists() and invalid.read_text(encoding="utf-8").strip():
    sys.exit(f"invalid record: {invalid.read_text(encoding='utf-8')[:300]}")
rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []
if len(rows) != 1:
    sys.exit(f"expected 1 record in {path}, found {len(rows)}")
row = rows[0]
if row.get("invalid"):
    sys.exit(f"invalid record: {row['invalid']}")
harness = row.get("harness") or {}
if kind == "longitudinal":
    if not row.get("steps"):
        sys.exit("the session recorded no step")
elif governed and kind == "embedded" and harness.get("outcome") == "no-run":
    sys.exit("no executionId: the host session started no run")
elif mode == "2" and harness.get("outcome") == "task-refused-exit-2":
    pass  # harness-core refuses a task without criteria (the casual prompt): dry_run.py "casual-core"
elif governed and kind == "matrix" and not harness.get("runId"):
    sys.exit(f"no executionId: outcome {harness.get('outcome')}")
PY
}

# smoke_cell <slot> <block> <kind> <condition> <mode, see check_smoke> <command...>: runs one dry cell and checks it.
smoke_cell() {
  local slot="$1" block="$2" kind="$3" condition="$4" governed="$5"
  shift 5
  local dir="$EVAL_WORK/smoke/$block"
  if ! PATH="$(slot_path "$slot")" "$@" >> "$dir/log-$condition.txt" 2>&1; then
    echo "SMOKE FAILED ($block, $condition): the dry cell exited with an error; see $dir/log-$condition.txt" >&2
    return 1
  fi
  if ! PATH="$(slot_path "$slot")" check_smoke "$slot" "$kind" "$dir/records-$condition.jsonl" "$governed" 2>&1 \
    | tee -a "$dir/log-$condition.txt" | grep -q .; then
    echo "smoke ok: $block $condition"
    return 0
  fi
  echo "SMOKE FAILED ($block, $condition); see $dir/log-$condition.txt" >&2
  return 1
}

run_smoke() {
  local block="$1" slot_name="$2"
  if [ "${EVAL_SKIP_SMOKE:-0}" = "1" ]; then
    log "smoke $block skipped (EVAL_SKIP_SMOKE=1)"
    return 0
  fi
  if [ "$block" = "rides" ]; then
    log "smoke rides skipped: run_rides.py has no dry run"
    return 0
  fi
  local slot model scenario dir work failed=0 condition governed
  slot=$(make_slot "$slot_name")
  model="${EVAL_SMOKE_MODEL:-${EVAL_MODELS%% *}}"
  scenario="${EVAL_SCENARIOS%% *}"
  dir="$EVAL_WORK/smoke/$block"
  rm -rf "$dir"
  mkdir -p "$dir"
  work=$(smoke_work "$block" "$model")
  smoke_clean "$work"
  log "smoke $block: one dry cell per condition ($model, $scenario)"
  case "$block" in
    A | B)
      local conditions prompt=full
      if [ "$block" = "A" ]; then
        conditions="${EVAL_CONDITIONS:-direct,harness},harness-anchored"
      else
        conditions="${EVAL_CONDITIONS:-direct,harness-core,harness}"
        prompt="${EVAL_PROMPTS:-casual poor}"
        prompt="${prompt%% *}"
      fi
      for condition in ${conditions//,/ }; do
        governed=1
        [ "$condition" = "direct" ] && governed=0
        [ "$condition" = "harness-core" ] && [ "$prompt" = "casual" ] && governed=2
        smoke_cell "$slot" "$block" matrix "$condition" "$governed" \
          "$slot/$VENV_BIN/python" "$CODE/run_eval.py" --scenario "$scenario" --condition "$condition" --model "$model" \
          --rep "$SMOKE_REP" --prompt "$prompt" --work "$work" --cache "$EVAL_CACHE" --out "$dir/records-$condition.jsonl" \
          --dry-run ${CORE_FLAG[@]+"${CORE_FLAG[@]}"} || failed=1
      done
      ;;
    T1)
      # label:prompt:condition:extra flag; the four conditions of the block (casual prompt, except the ceiling)
      local cell label cprompt ccond cextra
      for cell in direct:casual:direct: harness:casual:harness: nopo:casual:harness:--no-product-owner \
        ceiling:full:direct:; do
        IFS=: read -r label cprompt ccond cextra <<< "$cell"
        governed=1
        [ "$ccond" = "direct" ] && governed=0
        smoke_cell "$slot" "$block" matrix "$label" "$governed" \
          "$slot/$VENV_BIN/python" "$CODE/run_eval.py" --scenario "$scenario" --condition "$ccond" --model "$model" \
          --rep "$SMOKE_REP" --prompt "$cprompt" --work "$work" --cache "$EVAL_CACHE" --out "$dir/records-$label.jsonl" \
          --dry-run ${cextra:+$cextra} || failed=1
      done
      ;;
    LADDER)
      for condition in direct harness-core harness-nopanel harness; do
        governed=1
        [ "$condition" = "direct" ] && governed=0
        smoke_cell "$slot" "$block" matrix "$condition" "$governed" \
          "$slot/$VENV_BIN/python" "$CODE/run_eval.py" --scenario brownfield --condition "$condition" --model "$model" \
          --rep "$SMOKE_REP" --prompt full --work "$work" --cache "$EVAL_CACHE" --out "$dir/records-$condition.jsonl" \
          --dry-run ${CORE_FLAG[@]+"${CORE_FLAG[@]}"} || failed=1
      done
      ;;
    D)
      for condition in ${EVAL_D_CONDITIONS:-baseline structured harness}; do
        governed=1
        case "$condition" in baseline | structured) governed=0 ;; esac
        smoke_cell "$slot" "$block" longitudinal "$condition" "$governed" \
          "$slot/$VENV_BIN/python" "$CODE/longitudinal/run_session.py" --condition "$condition" --model "$model" \
          --rep "$SMOKE_REP" --work "$work" --out "$dir/records-$condition.jsonl" --dry-run || failed=1
      done
      ;;
    embedded)
      smoke_cell "$slot" "$block" embedded embedded 1 \
        "$slot/$VENV_BIN/python" "$CODE/run_embedded.py" --scenario "${EVAL_EMBEDDED_SCENARIO:-brownfield}" --model "$model" \
        --rep "$SMOKE_REP" --variant "${EVAL_EMBEDDED_VARIANT:-init}" --work "$work" --cache "$EVAL_CACHE" \
        --out "$dir/records-embedded.jsonl" --dry-run || failed=1
      ;;
    *)
      echo "ABORT: no smoke test for block '$block'" >&2
      exit 6
      ;;
  esac
  smoke_report_depth "$block" "$work"
  smoke_clean "$work"
  if [ "$failed" != "0" ]; then
    log "SMOKE FAILED in $block: no real call was made"
    exit 6
  fi
  log "smoke $block passed"
}
