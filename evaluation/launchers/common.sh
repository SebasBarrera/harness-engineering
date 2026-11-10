#!/usr/bin/env bash
# Shared setup of the 2.0.0 launchers (sourced by block-*.sh and pilot.sh; never run on its own).
#
# Environment (no machine path is written in these files):
#   EVAL_PYTHON      python of a virtual environment with the v2.0.0 wheel and the measuring tools
#                    (pytest 8.3.5, freezegun 1.4.0, coverage, ruff, bandit, mypy)        [required]
#   EVAL_WORK        work directory OUTSIDE the repository: runs, slot venvs, code copy    [required]
#   EVAL_CACHE       directory holding itsdangerous-2.2.0.tar.gz                           [required]
#   EVAL_WHEEL       the wheel EVAL_PYTHON's venv was installed from (SHA-256 recorded)    [required for real runs]
#   EVAL_EXTRA_PATH  directories (colon-separated) put first on the slots' import path, such as a
#                    development source tree instead of the wheel (logged)               [optional]
#   EVAL_OUT         results directory (default: evaluation/results-2.0.0/<block> of this checkout)
#   EVAL_MODELS      default: claude-haiku-4-5-20251001 claude-sonnet-5-5 claude-opus-5-5
#   EVAL_SCENARIOS   default: greenfield brownfield security
#   EVAL_REPS        default: 3
#   EVAL_PARALLEL    1 (default): the scenarios of a model run at the same time, one slot venv each;
#                    0: one run at a time
#   EVAL_DRY=1       dry run: fake_claude.py instead of Claude Code, no account check, no model call
#   EVAL_CLAUDE_ACCOUNT  the account every real call must use (default: the one in agentlib.py)
#   EVAL_CORE_REFERENCE_SRC  src/ of harness 1.0.0: harness-core runs compare their digest with it
set -u

LAUNCHERS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL_DIR="$(dirname "$LAUNCHERS")"
: "${EVAL_PYTHON:?set EVAL_PYTHON to the python of the evaluation venv}"
: "${EVAL_WORK:?set EVAL_WORK to a work directory outside the repository}"
: "${EVAL_CACHE:?set EVAL_CACHE to the directory with itsdangerous-2.2.0.tar.gz}"
EVAL_MODELS="${EVAL_MODELS:-claude-haiku-4-5-20251001 claude-sonnet-5-5 claude-opus-5-5}"
EVAL_SCENARIOS="${EVAL_SCENARIOS:-greenfield brownfield security}"
EVAL_REPS="${EVAL_REPS:-3}"
EVAL_PARALLEL="${EVAL_PARALLEL:-1}"
EVAL_DRY="${EVAL_DRY:-0}"
DRY_FLAG=()
[ "$EVAL_DRY" = "1" ] && DRY_FLAG=(--dry-run)
CORE_FLAG=()
[ -n "${EVAL_CORE_REFERENCE_SRC:-}" ] && CORE_FLAG=(--core-reference-src "$EVAL_CORE_REFERENCE_SRC")

# Keep the machine awake for the whole block (a sleeping machine breaks the agent calls).
keep_awake() {
  if [ -z "${EVAL_CAFFEINATED:-}" ] && command -v caffeinate > /dev/null; then
    EVAL_CAFFEINATED=1 exec caffeinate -dimsu /bin/bash "$@"
  fi
}

# Hard rule: real calls only on the evaluation account, no API key, no configuration override.
check_account() {
  if [ "$EVAL_DRY" = "1" ]; then
    echo "dry run: no Claude call, account not checked"
    return 0
  fi
  local expected="${EVAL_CLAUDE_ACCOUNT:-$("$EVAL_PYTHON" -c "import sys; sys.path.insert(0, '$EVAL_DIR'); import agentlib; print(agentlib.EXPECTED_ACCOUNT)")}"
  local account
  account=$(python3 -c "import json,os; print((json.load(open(os.path.expanduser('~/.claude.json'))).get('oauthAccount') or {}).get('emailAddress',''))")
  if [ "$account" != "$expected" ] || [ -n "${ANTHROPIC_API_KEY:-}" ] || [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    echo "ABORT: the Claude CLI is not authenticated with the evaluation account (found '$account') or an override is set" >&2
    exit 3
  fi
  echo "account checked"
}

# One run of a block at a time per work directory.
take_lock() {
  LOCK="$EVAL_WORK/$1.lock"
  mkdir -p "$EVAL_WORK"
  if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2> /dev/null; then
    echo "ABORT: $1 already running (pid $(cat "$LOCK"))" >&2
    exit 4
  fi
  echo $$ > "$LOCK"
  trap 'rm -f "$LOCK"' EXIT
}

# A copy of the evaluation code for the runner, and a directory with only the adapter for the
# harness to run: the implement request shows the agent the provider's command line, so the agent
# must not be pointed at a directory with the hidden tests and the reference solutions.
copy_code() {
  CODE="$EVAL_WORK/code"
  PROVIDER_CODE="$EVAL_WORK/provider-code"
  rm -rf "$CODE" "$PROVIDER_CODE"
  cp -R "$EVAL_DIR" "$CODE"
  find "$CODE" -name "__pycache__" -prune -exec rm -rf {} +
  rm -rf "$CODE/results" "$CODE/results-2.0.0" "$CODE/rides/hidden" "$CODE/rides/reference" \
    "$CODE/rides/reference_tests" "$CODE/large/hidden" "$CODE/large/reference"
  mkdir -p "$PROVIDER_CODE"
  cp "$EVAL_DIR/agentlib.py" "$EVAL_DIR/claude_provider.py" "$PROVIDER_CODE/"
  export EVAL_PROVIDER_CODE="$PROVIDER_CODE"
}

# A slot venv per concurrent run: the brownfield project is made importable through a .pth file in
# the venv's site-packages, so two runs must never share one.
make_slot() {
  local slot="$EVAL_WORK/venvs/slot-$1"
  if [ ! -x "$slot/bin/python" ]; then
    local base_python
    base_python=$("$EVAL_PYTHON" -c "import sys; print(sys._base_executable)")
    "$base_python" -m venv --without-pip "$slot" > /dev/null
  fi
  local base_site
  base_site=$("$EVAL_PYTHON" -c "import sys; print(next(p for p in sys.path if p.endswith('site-packages')))")
  local own_site
  own_site=$("$slot/bin/python" -c "import sys; print(next(p for p in sys.path if p.endswith('site-packages')))")
  { [ -n "${EVAL_EXTRA_PATH:-}" ] && echo "$EVAL_EXTRA_PATH" | tr ':' '\n'; echo "$base_site"; } > "$own_site/00_eval_base.pth"
  cat > "$slot/bin/harness" << 'SH'
#!/bin/sh
'''exec' "$(dirname "$0")/python" "$0" "$@"
' '''
import sys

from governed_harness.cli.main import main

sys.exit(main())
SH
  chmod +x "$slot/bin/harness"
  echo "$slot"
}

# The PATH of a run: the slot first (python, harness), then the Claude Code CLI and the system.
slot_path() {
  local claude_dir=""
  command -v claude > /dev/null && claude_dir="$(dirname "$(command -v claude)"):"
  echo "$1/bin:${claude_dir}/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
}

# environment.json of the block: harness version and wheel SHA-256, Claude Code, tools, code digest.
record_environment() {
  local slot
  slot=$(make_slot env)
  local wheel=()
  [ -n "${EVAL_WHEEL:-}" ] && wheel=(--wheel "$EVAL_WHEEL")
  local source=()
  if [ -n "${EVAL_EXTRA_PATH:-}" ]; then
    # A development source tree instead of the wheel (the pilot): its commit is recorded.
    source=(--source "${EVAL_EXTRA_PATH%%:*}")
  elif [ "$EVAL_DRY" != "1" ] && [ -z "${EVAL_WHEEL:-}" ]; then
    echo "ABORT: set EVAL_WHEEL to the v2.0.0 wheel the evaluation venv was installed from" >&2
    exit 5
  fi
  PATH="$(slot_path "$slot")" "$slot/bin/python" "$CODE/environment.py" "$EVAL_OUT/environment.json" \
    ${wheel[@]+"${wheel[@]}"} ${source[@]+"${source[@]}"} --code "$CODE" --block "$1"
  [ -n "${EVAL_EXTRA_PATH:-}" ] && echo "EVAL_EXTRA_PATH set: the harness comes from a source tree, not the wheel" \
    | tee -a "$EVAL_OUT/log.txt"
  unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT PYTHONPATH
}

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$EVAL_OUT/log.txt"; }
