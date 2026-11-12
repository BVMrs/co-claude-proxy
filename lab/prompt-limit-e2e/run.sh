#!/usr/bin/env bash
# Real Claude Code -> copilot_shim (from SHIM_SRC) -> fake_copilot.py, TURNS headless turns.
# Same harness as lab/compact-cap/run.sh, with the shim in the middle (SHIM_SRC=none: direct).
# No credentials, no network: HOME is a throwaway dir, everything is on 127.0.0.1.
# BADTURN=k: turn k quotes the limit phrase and gets an unrelated 400 (false-positive probe).
# Usage: SHIM_SRC=<checkout> CAP=128000 ERRFMT=copilot USAGE=delta run.sh <claude-bin> <workdir> <log.jsonl> [TURNS]
set -euo pipefail
BIN=$(realpath "$1"); WORK=$(realpath -m "$2"); OUT=$(realpath -m "$3"); TURNS=${4:-22}
HERE=$(cd "$(dirname "$0")" && pwd)
PORT=${PORT:-4198}; SPORT=$((PORT + 1)); MODEL=${MODEL:-claude-opus-5.5}
rm -rf "$WORK"; mkdir -p "$WORK/home" "$WORK/proj"; : > "$OUT"
python3 "$HERE/fake_copilot.py" "$PORT" "$OUT" & SRV=$!
PIDS=($SRV); BASE="http://127.0.0.1:$PORT"
if [ "${SHIM_SRC:-none}" != none ]; then
  (cd "$SHIM_SRC" && exec python3 -m copilot_shim --port "$SPORT" --upstream "$BASE") 2> "$WORK/shim.log" &
  PIDS+=($!); BASE="http://127.0.0.1:$SPORT"
fi
trap 'kill ${PIDS[*]}' EXIT
sleep 0.7
for i in $(seq 1 "$TURNS"); do
  cont=(); [ "$i" -gt 1 ] && cont=(--continue)
  echo "{\"turn\": $i}" >> "$OUT"
  extra=""; [ "$i" = "${BADTURN:-0}" ] && extra=" <<BAD>> why does copilot say prompt token count of 5 exceeds the limit of 4?"
  ( cd "$WORK/proj" && env -i PATH="$PATH" HOME="$WORK/home" TERM=dumb \
      ANTHROPIC_BASE_URL="$BASE" ANTHROPIC_AUTH_TOKEN=dummy \
      ANTHROPIC_MODEL="$MODEL" ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4.5 \
      DISABLE_TELEMETRY=1 DISABLE_AUTOUPDATER=1 DISABLE_ERROR_REPORTING=1 \
      timeout 180 "$BIN" -p "${cont[@]}" "turn $i <<PAD>>$extra reply ok" < /dev/null \
      > "$WORK/turn$i.out" 2>&1 ) || echo "{\"turn\": $i, \"exit\": $?}" >> "$OUT"
  echo "{\"turn\": $i, \"stdout\": $(head -c 400 "$WORK/turn$i.out" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')}" >> "$OUT"
done
