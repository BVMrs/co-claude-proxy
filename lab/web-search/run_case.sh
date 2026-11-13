#!/usr/bin/env bash
# One headless Claude Code run against capture_server.py (optionally through the shim).
# Usage: [WS_MODE=..] [VIA_SHIM=1] run_case.sh <claude-bin> <workdir> <out.jsonl> <prompt> [extra env KEY=VAL ...]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
BIN=$1 W=$2 OUT=$3 PROMPT=$4; shift 4
PORT=${PORT:-4199}; SPORT=$((PORT+1))
rm -rf "$W"; mkdir -p "$W/home" "$W/proj"; : > "$OUT"
(cd "$W/proj" && git init -q)
cat > "$W/home/.claude.json" <<J
{"hasCompletedOnboarding": true, "numStartups": 5,
 "projects": {"$W/proj": {"hasTrustDialogAccepted": true, "hasCompletedProjectOnboarding": true}}}
J
WS_MODE=${WS_MODE:-anthropic} python3 "$HERE/capture_server.py" "$PORT" "$OUT" & SRV=$!
URL=http://127.0.0.1:$PORT
if [ -n "${VIA_SHIM:-}" ]; then
  (cd "$ROOT" && python3 -m copilot_shim --port $SPORT --upstream $URL 2> "$W/shim.log") & SHIM=$!
  URL=http://127.0.0.1:$SPORT
fi
trap 'kill $SRV ${SHIM:-} 2>/dev/null' EXIT
sleep 0.7
cd "$W/proj"
start=$(date +%s%N)
env -i PATH="$PATH" HOME="$W/home" TERM=dumb ANTHROPIC_BASE_URL="$URL" \
  ANTHROPIC_AUTH_TOKEN=dummy ANTHROPIC_MODEL=claude-opus-5.5 DISABLE_TELEMETRY=1 \
  DISABLE_AUTOUPDATER=1 DISABLE_ERROR_REPORTING=1 "$@" \
  timeout 180 "$BIN" -p "$PROMPT" --allowedTools 'WebSearch' --output-format json > "$W/result.json" 2> "$W/stderr.log" || echo "exit $?"
echo "wall_ms $(( ($(date +%s%N) - start) / 1000000 ))" > "$W/wall.txt"
