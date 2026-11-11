#!/usr/bin/env bash
# Reproduce the brownfield case of the thesis on pallets/itsdangerous 2.2.0.
#
# Usage: examples/brownfield-itsdangerous/reproduce.sh <harness-wheel-or-requirement> [workdir]
# Needs: Python >= 3.12 (set PYTHON=/path/to/python3.12 if python3 is older), Git, curl,
# network access to github.com and to a Python package index.
# Expected exit codes with the defaults `harness init` writes: run start 4 (the baseline already
# fails without freezegun; environment.baseline: report records it and verification.differential
# classifies the failures as pre-existing, so the gate PASSED), findings list 0, the project's
# suite run by hand after installing freezegun 0, APPROVE 0.
# On Windows, Git Bash accepts the work directory as /c/... or C:/....
set -euo pipefail

HARNESS_SPEC="${1:?usage: reproduce.sh <harness wheel path or requirement> [workdir]}"
WORKDIR="${2:-$(mktemp -d)}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REQ="$HERE/../../requirements"   # hash-pinned installs (scripts/lock_requirements.py)
PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))' || { echo "Python >= 3.12 required (set PYTHON)"; exit 2; }
ARCHIVE_URL="https://github.com/pallets/itsdangerous/archive/refs/tags/2.2.0.tar.gz"
ARCHIVE_SHA256="7b0c6d4186e963b88489b69603b7ab2bf7c8e9eb4135a7b13b5f21bd4b937f2b"

expect() { # expect <code> <command...>
  local want="$1"; shift
  set +e; "$@" > "$WORKDIR/last.out" 2>&1; local got=$?; set -e
  printf '%-4s exit %-3s (expected %s)  %s\n' "$([ "$got" = "$want" ] && echo ok || echo BAD)" "$got" "$want" "$*"
  [ "$got" = "$want" ] || { cat "$WORKDIR/last.out"; exit 1; }
}

mkdir -p "$WORKDIR"
# Absolute POSIX form: a Windows drive path (C:/...) would split PATH at its colon.
WORKDIR="$(cd "$WORKDIR" && pwd)"
cd "$WORKDIR"
# Keep the run registry (runtime.stateDir: auto) in the work directory, outside the workspace:
# nothing goes to the user's application-data directory and no run of an earlier invocation in the
# same directory can collide. A native Windows Python gets it as C:/... (cygpath, Git Bash).
HARNESS_STATE_DIR="$WORKDIR/state"
command -v cygpath > /dev/null && HARNESS_STATE_DIR="$(cygpath -m "$HARNESS_STATE_DIR")"
export HARNESS_STATE_DIR
curl -sSfL -o itsdangerous-2.2.0.tar.gz "$ARCHIVE_URL"
echo "$ARCHIVE_SHA256  itsdangerous-2.2.0.tar.gz" | shasum -a 256 -c -
tar -xzf itsdangerous-2.2.0.tar.gz

"$PYTHON" -m venv venv
BIN=venv/bin; [ -d venv/Scripts ] && BIN=venv/Scripts   # POSIX or Windows (Git Bash)
# The harness's dependencies and pytest 8.1.1 come from hash-pinned files; the harness itself and
# the project under test are installed without resolving anything.
"$BIN/python" -m pip install -q --require-hashes -r "$REQ/runtime.txt"
"$BIN/python" -m pip install -q --require-hashes -r "$REQ/example-pytest.txt"
"$BIN/python" -m pip install -q --no-deps "$HARNESS_SPEC"
"$BIN/python" -m pip install -q -e ./itsdangerous-2.2.0 --no-deps   # the project under test
export PATH="$WORKDIR/$BIN:$PATH"

cd itsdangerous-2.2.0
git init -q && git add . && git -c user.name=demo -c user.email=demo@example.invalid -c commit.gpgsign=false \
  commit -qm "itsdangerous 2.2.0 (GitHub tag archive)"

expect 0 harness init --path .
expect 0 harness inspect --path .
expect 0 harness task create --path . --file "$HERE/task.yaml"
expect 4 harness run start --path . --task task_itsdangerous_base64_non_ascii
cp "$WORKDIR/last.out" ../run.json
RUN=$(python -c "import json;print(json.load(open('../run.json'))['executionId'])")
expect 0 harness findings list --path . --run "$RUN"
# freezegun, declared in requirements/tests.txt, is missing: pytest stops while collecting on the
# baseline and on the change alike, so the failures are pre-existing and the gate PASSED.
python - "$WORKDIR/last.out" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
rules = {f["ruleId"] for f in (data if isinstance(data, list) else data["findings"])}
want = {"environment.baseline-failing", "differential.preexisting", "python.pytest.failed"}
missing = want - rules
print("findings", ", ".join(sorted(want & rules)), f"MISSING {sorted(missing)}" if missing else "")
sys.exit(1 if missing else 0)
PY
harness status --path . --run "$RUN" | python -c "
import json,sys; g=json.load(sys.stdin)['gate']['status']; print('gate', g); sys.exit(g != 'PASSED')"

# No test ran in that attempt, so the reviewer repairs the environment (not the ChangeSet) and runs
# the project's suite on the change before deciding. `run continue` would not verify again: the
# run already waits at DECISION.
python -m pip install -q --require-hashes -r "$REQ/example-freezegun.txt"
expect 0 python -m pytest -q -p no:cacheprovider
tail -1 "$WORKDIR/last.out"
DIGEST=$(harness status --path . --run "$RUN" | python -c "import json,sys;print(json.load(sys.stdin)['execution']['changeSetDigest'])")
expect 0 harness gate decide --path . --run "$RUN" --decision APPROVE --change-set-digest "$DIGEST" \
  --actor human.reviewer \
  --rationale "The baseline failures (freezegun missing) are pre-existing; with freezegun installed the project's suite passes on the same ChangeSet"
harness status --path . --run "$RUN" | python -c "
import json,sys; d=json.load(sys.stdin); e=d['execution']
print('status', e['status'], '| changeSet', e['changeSetDigest'], '| events', d['eventCount'], '| chain valid', d['eventChainValid'])"
