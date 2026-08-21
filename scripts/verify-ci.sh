#!/usr/bin/env bash
# verify-ci.sh — run CI EXACTLY as GitHub does, locally, before pushing.
#
#   scripts/verify-ci.sh            full parity run (fresh checkout, clean venv,
#                                   -m "not ai" incl. slow tests, coverage gate)
#
# Why a fresh worktree: local trees carry built KBs, local config.json and
# stale caches that mask fresh-checkout failures (three CI incidents came
# from exactly that). This script checks out the commit into a temp
# worktree — no local state can leak in.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"
COMMIT="${1:-HEAD}"

echo "[verify-ci] commit: $(git rev-parse --short "$COMMIT")"
WORKTREE=$(mktemp -d /tmp/suijin-ci.XXXXXX)
VENV=$(mktemp -d /tmp/suijin-civenv.XXXXXX)
cleanup() { git worktree remove --force "$WORKTREE" 2>/dev/null || rm -rf "$WORKTREE"; rm -rf "$VENV"; }
trap cleanup EXIT

git worktree add -q --detach "$WORKTREE" "$COMMIT"

echo "[verify-ci] building clean venv (python3)"
python3 -m venv "$VENV/venv"
"$VENV/venv/bin/python" -m pip install --quiet --upgrade pip
echo "[verify-ci] installing requirements + test deps (same set as the workflow)"
"$VENV/venv/bin/python" -m pip install --quiet -r "$WORKTREE/suijin/requirements.txt" pytest pytest-asyncio pytest-cov

cd "$WORKTREE"
echo "[verify-ci] pytest -m 'not ai' --cov (the exact CI step)"
"$VENV/venv/bin/python" -m pytest suijin/tests/ -q --tb=short \
    --cov=suijin --cov-report=term-missing --cov-report=xml \
    -m "not ai" -p no:warnings

echo "[verify-ci] ruff (the exact CI step)"
"$VENV/venv/bin/python" -m pip install --quiet ruff
"$VENV/venv/bin/python" -m ruff check suijin/

echo "[verify-ci] deploy-scripts sanity (the exact CI step)"
bash -n install.sh && bash -n docker.sh && bash -n kali-setup.sh
python3 - <<'PYEOF'
import re
import yaml

compose = yaml.safe_load(open("docker-compose.yml"))
env_list = compose["services"]["suijin"]["environment"]
compose_keys = {m.group(1) for e in env_list for m in [re.match(r"([A-Z_]+)=", str(e))] if m}
example_keys = set(re.findall(r"^([A-Z_]+)=", open(".env.example").read(), re.M))
assert compose_keys == example_keys, f"env drift: {compose_keys ^ example_keys}"
print(f"deploy sanity ok: {len(compose_keys)} env keys match, compose parses")
PYEOF

echo
echo "[verify-ci] GREEN — this is what CI will do on this commit."
