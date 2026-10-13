#!/bin/bash
# FULL local safety net — the "Suijin Backup" disk partition.
#
# OPERATOR RULE (2026-10-12, this session and every session after):
# run this after EVERY substantive round. No exceptions, no git push
# involved. It mirrors, replacing previous content:
#   /Volumes/Suijin Backup/Suijin        <- the repo (incl. .git,
#                                          agent-vault/, suijin/.env)
#   /Volumes/Suijin Backup/suijin-home   <- ~/.suijin (workspace with
#                                          ALL engagement data, keys,
#                                          mesh), minus venv/ + repo
#                                          symlink (recreate-able)
# BACKUP_INFO.txt records what each mirror IS (date, HEAD, dirty paths)
# so a future restore knows exactly what it holds.
#
# Wipe context: an old AI ran `sudo rm -rf ~/` inside a container whose
# ~ was OrbStack-bound to the real home; engagement data died with it.
# This script is the answer. If the partition is missing, FAIL LOUD.
set -euo pipefail

SRC="${SUIJIN_SRC:-/Users/williamjiang/suijin}"
HOME_DIR="${SUIJIN_HOME_DIR:-/Users/williamjiang/.suijin}"
PART=""
for cand in "/Volumes/Suijin Backup" "/Volumes/Backup"; do
  if [ -d "$cand" ]; then PART="$cand"; break; fi
done

if [ -z "$PART" ]; then
  echo "ERROR: backup partition ('Suijin Backup' / 'Backup') is NOT mounted — nothing backed up." >&2
  exit 1
fi

# ONE-GENERATION ROTATION (the 2026-10-12 lesson: a single mirror means
# one bad run overwrites the only good copy — the rollback round's
# backup erased the state we then needed back). .prev = last-known-good.
for name in Suijin suijin-home; do
  if [ -d "$PART/$name" ]; then
    rm -rf "$PART/$name.prev"
    mv "$PART/$name" "$PART/$name.prev"
  fi
done

DEST="$PART/Suijin"
mkdir -p "$DEST"

# 1) the repo mirror
rsync -a --delete --delete-excluded \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.DS_Store' \
  --exclude 'suijin/suijin_agent' \
  --exclude '/suijin_agent' \
  "$SRC/" "$DEST/"

# 2) the agent home: workspace (engagement data), keys, mesh — all of it
if [ -d "$HOME_DIR" ]; then
  mkdir -p "$PART/suijin-home"
  rsync -a --delete --delete-excluded \
    --exclude 'venv/' \
    --exclude 'repo' \
    --exclude '__pycache__/' \
    --exclude '.DS_Store' \
    "$HOME_DIR/" "$PART/suijin-home/"
fi

# Manifest — what this mirror IS.
{
  echo "backup: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "src:    $SRC"
  if git -C "$SRC" rev-parse HEAD >/dev/null 2>&1; then
    echo "head:   $(git -C "$SRC" rev-parse HEAD)"
    echo "branch: $(git -C "$SRC" rev-parse --abbrev-ref HEAD)"
    echo "dirty:  $(git -C "$SRC" status --porcelain | wc -l | tr -d ' ') changed paths"
    git -C "$SRC" status --porcelain | head -20 | sed 's/^/        /'
  else
    echo "head:   (no git)"
  fi
  echo "home:   $HOME_DIR -> $PART/suijin-home"
} > "$DEST/BACKUP_INFO.txt"

echo "ok: full backup complete"
du -sh "$DEST" "$PART/suijin-home" 2>/dev/null
