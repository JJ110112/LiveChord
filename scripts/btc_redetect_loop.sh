#!/usr/bin/env bash
# Run scripts/btc_redetect_pc.py in chunks, restarting the Python
# process between chunks so accumulated librosa / numpy / madmom
# buffers in the main process get reclaimed by the OS. After ~2k tracks
# the long-running variant hit MemoryError in librosa loads despite
# 64-bit Python + plenty of GPU memory — the host RAM footprint kept
# climbing inside the same interpreter. Bulletproof workaround: exit
# the interpreter and start a fresh one.
#
# Each chunk:
#   * resolves the current missing-chord work list (so re-scans handle
#     anything written by a prior chunk)
#   * processes up to $CHUNK tracks
#   * exits — the wrapper loop kicks off another chunk
#
# Stops when a chunk reports "Nothing to do."
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHUNK="${CHUNK:-500}"
LOG="${LOG:-V:/data/btc_redetect_run.log}"
PROGRESS="${PROGRESS:-V:/data/btc_redetect_progress.jsonl}"
DATA_ROOT="${DATA_ROOT:-V:/data}"

i=0
while :; do
  i=$((i + 1))
  echo "=============================================================" >> "$LOG"
  echo "[wrapper] chunk #$i begin $(date -Iseconds)" >> "$LOG"
  echo "=============================================================" >> "$LOG"
  python -u "$REPO_ROOT/scripts/btc_redetect_pc.py" \
      --limit "$CHUNK" \
      --data-root "$DATA_ROOT" \
      --progress-file "$PROGRESS" >> "$LOG" 2>&1
  rc=$?
  echo "[wrapper] chunk #$i end rc=$rc $(date -Iseconds)" >> "$LOG"

  # Did this chunk find anything to do? Look for "Nothing to do." in the
  # tail; if so, the work list is empty → exit the loop.
  if tail -50 "$LOG" | grep -q "^Nothing to do\.$"; then
    echo "[wrapper] done — no missing tracks remain" >> "$LOG"
    break
  fi
  if [ "$rc" -ne 0 ]; then
    echo "[wrapper] chunk #$i exited non-zero (rc=$rc); retrying" >> "$LOG"
  fi
done
