#!/usr/bin/env bash
# Find the retry-after value above which Claude Code stops waiting and fails at once.
# A run still waiting at TMO exits 124 (= Claude Code honours the header); exit 1 after one request = fail fast.
# Usage: threshold.sh <claude-bin> <workdir> [seconds ...]
set -u
BIN=$1; WORK=$2; shift 2; HERE=$(cd "$(dirname "$0")" && pwd); mkdir -p "$WORK"
VALS=("${@:-45 59 60 60.5 61 120 300 3600}"); VALS=(${VALS[@]}); p=4250
for ra in "${VALS[@]}"; do p=$((p+1))
  PORT=$p FAIL=always RETRY_AFTER=$ra TMO=20 "$HERE/run_one.sh" "$BIN" "$WORK" "ra$ra" & done; wait
for ra in "${VALS[@]}"; do echo "retry-after=$ra requests=$(wc -l < "$WORK/ra$ra.jsonl") $(cat "$WORK/ra$ra.meta")"; done
