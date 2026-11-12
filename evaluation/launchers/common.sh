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
#
# Windows (Git Bash, the 2.0.0 pilot machine): EVAL_EXTRA_PATH is semicolon-separated (Windows paths
# hold a colon), the slot venvs keep their programs in Scripts/ with a harness.exe launcher, the run
# PATH lists Git's cmd/ and the Windows system directories, PYTHONUTF8=1 is exported, and the
# governed conditions run with runtime.agentSandbox off (Windows has no OS write sandbox; declared
# in environment.json and log.txt). Every other system keeps the POSIX branch unchanged.
set -u

LAUNCHERS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL_DIR="$(dirname "$LAUNCHERS")"
EVAL_WINDOWS=0
VENV_BIN=bin
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*)
    EVAL_WINDOWS=1
    VENV_BIN=Scripts
    # The Windows form (C:/...): these paths also reach Python inside program text.
    LAUNCHERS="$(cygpath -m "$LAUNCHERS")"
    EVAL_DIR="$(cygpath -m "$EVAL_DIR")"
    export PYTHONUTF8=1
    ;;
esac
: "${EVAL_PYTHON:?set EVAL_PYTHON to the python of the evaluation venv}"
: "${EVAL_WORK:?set EVAL_WORK to a work directory outside the repository}"
: "${EVAL_CACHE:?set EVAL_CACHE to the directory with itsdangerous-2.2.0.tar.gz}"

# Windows: the whole path of the files a run writes must stay under MAX_PATH (260). The longest one
# is the artifact store's temporary file of a run (src/governed_harness/evidence/artifact_store.py,
# _atomic_write): <run dir>/state/<project key>/artifacts/metadata/.<64 hex>.json.<8 random>, and the
# run registry sits in <run dir>/state (HARNESS_STATE_DIR). Measured: with a 28-character EVAL_WORK
# (a directory under the user profile) the state directory of a run was 146 characters long and the
# run failed; with the 5-character C:/e/b it worked. The tail below the state directory is
#   21 ("/" + project key "project-<12 hex>") + 10 ("/artifacts") + 9 ("/metadata") + 1 ("/")
#   + 79 ("." + 64 hex + ".json" + "." + 8 random characters of mkstemp) = 120
# so the state directory may have at most 260 - 120 = 140 characters (146 + 120 = 266 > 260 fails,
# 126 + 120 = 246 works, as observed). The state directory is
#   EVAL_WORK + "/runs/" + <block> + "/" + <model> + "/" + <run name> + "/state"
# with the longest of each: block "block-embedded" (14; "block-temptations", 17, has run names of
# about 31 characters, far shorter), model "claude-haiku-4-5-20251001" (25), and the run name
# <scenario>[-<prompt>]-<condition>-<model>-r<rep>-<timestamp>. The longest condition names are
# "harness-anchored" (16) and "harness-nopanel" (15), but they run only with the full prompt (no
# prompt tag: blocks A and LADDER), so "brownfield-harness-anchored" is 27 characters; the longest
# prefix is a casual cell of "harness-core" or "harness-nopo" (12, blocks B and T1), 30 characters:
#   "brownfield" (10) + "-casual" (7) + "-" + "harness-nopo" (12) + "-" + model (25) + "-r10" (4)
#   + "-" + "20261111T235959Z" (16) = 77.
# (A casual cell of harness-anchored would be 81 characters and need EVAL_WORK of 6 or less.)
# Fixed part: 6 ("/runs/") + 14 + 1 + 25 + 1 + 77 + 6 ("/state") = 130, so EVAL_WORK may have at most
# 140 - 130 = 10 characters in its Windows form (cygpath -w; backslashes count as one, like slashes).
EVAL_WORK_MAX_WINDOWS=10
if [ "$EVAL_WINDOWS" = "1" ]; then
  EVAL_WORK_WINDOWS="$(cygpath -w "$EVAL_WORK")"
  if [ "${#EVAL_WORK_WINDOWS}" -gt "$EVAL_WORK_MAX_WINDOWS" ]; then
    echo "ABORT: EVAL_WORK is ${#EVAL_WORK_WINDOWS} characters long ($EVAL_WORK_WINDOWS); on Windows it may have at most" \
      "$EVAL_WORK_MAX_WINDOWS or the paths of a run exceed the 260-character limit. Use a short one, such as C:/e/b." >&2
    exit 5
  fi
fi
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
  local py=python3
  [ "$EVAL_WINDOWS" = "1" ] && py="$EVAL_PYTHON"
  account=$("$py" -c "import json,os; print((json.load(open(os.path.expanduser('~/.claude.json'))).get('oauthAccount') or {}).get('emailAddress',''))")
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
  if [ "$EVAL_WINDOWS" = "1" ]; then
    make_slot_windows "$1"
    return
  fi
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

# make_slot on Windows: the same venv and .pth file; Python prints Windows paths, and the harness
# launcher is a harness.exe (pip's own launcher, distlib's ScriptMaker, bound to the slot's python),
# which Git Bash and CreateProcess (run_eval.py starts "harness") both find.
make_slot_windows() {
  local slot="$EVAL_WORK/venvs/slot-$1"
  if [ ! -x "$slot/Scripts/python.exe" ]; then
    local base_python
    base_python=$("$EVAL_PYTHON" -c "import sys; print(sys._base_executable)")
    "$(cygpath -u "$base_python")" -m venv --without-pip "$slot" > /dev/null
  fi
  local base_site
  base_site=$("$EVAL_PYTHON" -c "import sys; print(next(p for p in sys.path if p.endswith('site-packages')))")
  local own_site
  own_site=$("$slot/Scripts/python.exe" -c "import sys; print(next(p for p in sys.path if p.endswith('site-packages')))")
  { [ -n "${EVAL_EXTRA_PATH:-}" ] && echo "$EVAL_EXTRA_PATH" | tr ';' '\n'; echo "$base_site"; } \
    > "$(cygpath -u "$own_site")/00_eval_base.pth"
  "$EVAL_PYTHON" -c "
import sys
from pip._vendor.distlib.scripts import ScriptMaker
maker = ScriptMaker(None, sys.argv[1])
maker.executable = sys.argv[2]
maker.variants = {''}
maker.clobber = True
maker.make('harness = governed_harness.cli.main:main')
" "$(cygpath -w "$slot/Scripts")" "$(cygpath -w "$slot/Scripts/python.exe")" > /dev/null
  echo "$slot"
}

# The PATH of a run: the slot first (python, harness), then the Claude Code CLI and the system.
slot_path() {
  local claude_dir=""
  command -v claude > /dev/null && claude_dir="$(dirname "$(command -v claude)"):"
  if [ "$EVAL_WINDOWS" = "1" ]; then
    # Git Bash form; it becomes a Windows PATH for the programs it starts. Git's cmd/ lets Claude
    # Code find Git Bash for its Bash tool and the harness find git.
    local root
    root="$(cygpath -u "${SYSTEMROOT:-C:/Windows}")"
    echo "$(cygpath -u "$1")/Scripts:${claude_dir}/cmd:/usr/bin:$root/System32:$root:$root/System32/Wbem:$root/System32/WindowsPowerShell/v1.0"
    return
  fi
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
    if [ "$EVAL_WINDOWS" = "1" ]; then
      source=(--source "${EVAL_EXTRA_PATH%%;*}")
    else
      source=(--source "${EVAL_EXTRA_PATH%%:*}")
    fi
  elif [ "$EVAL_DRY" != "1" ] && [ -z "${EVAL_WHEEL:-}" ]; then
    echo "ABORT: set EVAL_WHEEL to the v2.0.0 wheel the evaluation venv was installed from" >&2
    exit 5
  fi
  PATH="$(slot_path "$slot")" "$slot/$VENV_BIN/python" "$CODE/environment.py" "$EVAL_OUT/environment.json" \
    ${wheel[@]+"${wheel[@]}"} ${source[@]+"${source[@]}"} --code "$CODE" --block "$1"
  [ -n "${EVAL_EXTRA_PATH:-}" ] && echo "EVAL_EXTRA_PATH set: the harness comes from a source tree, not the wheel" \
    | tee -a "$EVAL_OUT/log.txt"
  [ "$EVAL_WINDOWS" = "1" ] && echo "Windows: runtime.agentSandbox off in the governed conditions (no OS sandbox on Windows; harness init writes enforce)" \
    | tee -a "$EVAL_OUT/log.txt"
  unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT PYTHONPATH
}

# finish_environment <block>: Claude Code version at the end of the block. If it differs from the
# start, the block is flagged in environment.json (claudeCodeChanged) and the log warns; no result
# is deleted.
finish_environment() {
  local slot out
  slot=$(make_slot env)
  out=$(PATH="$(slot_path "$slot")" "$slot/$VENV_BIN/python" "$CODE/environment.py" "$EVAL_OUT/environment.json"     --block "$1" --finish)
  log "$out"
  case "$out" in
    *"changed=True"*) log "WARNING: Claude Code changed during block $1: the block is flagged (claudeCodeChanged), results kept" ;;
  esac
}

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$EVAL_OUT/log.txt"; }
