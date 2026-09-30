#!/usr/bin/env bash
# Reproduce the brownfield case of the thesis on pallets/itsdangerous 2.2.0.
#
# Usage: examples/brownfield-itsdangerous/reproduce.sh <harness-wheel-or-requirement> [workdir]
# Needs: Python >= 3.12 (set PYTHON=/path/to/python3.12 if python3 is older), Git, curl,
# network access to github.com and to a Python package index.
# Expected exit codes: run start 6 (baseline broken: freezegun missing), run continue 4,
# APPROVE 5 (gate FAILED by the finding of attempt 1, issue #2), APPROVE_EXCEPTION 0.
set -euo pipefail

HARNESS_SPEC="${1:?usage: reproduce.sh <harness wheel path or requirement> [workdir]}"
WORKDIR="${2:-$(mktemp -d)}"
HERE="$(cd "$(dirname "$0")" && pwd)"
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

mkdir -p "$WORKDIR" && cd "$WORKDIR"
curl -sSfL -o itsdangerous-2.2.0.tar.gz "$ARCHIVE_URL"
echo "$ARCHIVE_SHA256  itsdangerous-2.2.0.tar.gz" | shasum -a 256 -c -
tar -xzf itsdangerous-2.2.0.tar.gz

"$PYTHON" -m venv venv
venv/bin/python -m pip install -q "$HARNESS_SPEC" "pytest==8.1.1"
venv/bin/python -m pip install -q -e ./itsdangerous-2.2.0 --no-deps   # the project under test
export PATH="$WORKDIR/venv/bin:$PATH"

cd itsdangerous-2.2.0
git init -q && git add . && git -c user.name=demo -c user.email=demo@example.invalid -c commit.gpgsign=false \
  commit -qm "itsdangerous 2.2.0 (GitHub tag archive)"

expect 0 harness init --path .
expect 0 harness inspect --path .
expect 0 harness task create --path . --file "$HERE/task.yaml"
set +e; harness run start --path . --task task_itsdangerous_base64_non_ascii > ../run.json; rc=$?; set -e
echo "run start exit $rc (expected 6: freezegun, declared in requirements/tests.txt, is not installed)"
[ "$rc" = 6 ]
RUN=$(python -c "import json;print(json.load(open('../run.json'))['executionId'])")
expect 0 harness findings list --path . --run "$RUN"

python -m pip install -q "freezegun==1.4.0"   # fix the environment, not the ChangeSet
expect 4 harness run continue --path . --run "$RUN"
DIGEST=$(harness status --path . --run "$RUN" | python -c "import json,sys;print(json.load(sys.stdin)['execution']['changeSetDigest'])")
expect 5 harness gate decide --path . --run "$RUN" --decision APPROVE --change-set-digest "$DIGEST" \
  --actor human.reviewer --rationale "Plain approval"
expect 0 harness gate decide --path . --run "$RUN" --decision APPROVE_EXCEPTION --change-set-digest "$DIGEST" \
  --actor human.reviewer \
  --rationale "Attempt 1 failed only because freezegun was missing; all project tests pass with the same ChangeSet"
harness status --path . --run "$RUN" | python -c "
import json,sys; d=json.load(sys.stdin); e=d['execution']
print('status', e['status'], '| changeSet', e['changeSetDigest'], '| events', d['eventCount'], '| chain valid', d['eventChainValid'])"
grep -rh -E '[0-9]+ passed' .harness/artifacts | tail -1
