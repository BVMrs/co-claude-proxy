#!/usr/bin/env bash
# Claude Code 2.1.321 -> shim (main / prototype / none) -> copilot_sim.ts (copilot-api 0.7.0
# translators + scripted fake Copilot). No credentials, no network.
# DENY=1 writes permissions.deny ["WebSearch"] to the throwaway user settings.
# Usage: COPILOT_WS=call|callq|text|reject SHIM=main|proto|none \
#          run_chain.sh <claude-bin> <copilot-api checkout> <workdir> [extra env K=V ...]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
BIN=$1 API=$(realpath "$2") W=$(realpath -m "$3"); shift 3
PORT=${PORT:-4201}; SPORT=$((PORT+1)); SHIM=${SHIM:-main}
PROMPT=${PROMPT:-"When was Zork first released? Search the web."}
rm -rf "$W"; mkdir -p "$W/home" "$W/proj"; : > "$W/copilot.jsonl"
(cd "$W/proj" && git init -q)
if [ -n "${DENY:-}" ]; then mkdir -p "$W/home/.claude"; echo '{"permissions": {"deny": ["WebSearch"]}}' > "$W/home/.claude/settings.json"; fi
cat > "$W/home/.claude.json" <<J
{"hasCompletedOnboarding": true, "numStartups": 5,
 "projects": {"$W/proj": {"hasTrustDialogAccepted": true, "hasCompletedProjectOnboarding": true}}}
J
bun "$HERE/copilot_sim.ts" "$API" $PORT "$W/copilot.jsonl" > "$W/sim.log" 2>&1 & SIM=$!
URL=http://127.0.0.1:$PORT
case $SHIM in
  main)  (cd "$ROOT" && exec python3 -m copilot_shim --port $SPORT --upstream $URL 2> "$W/shim.log") & SH=$!
         URL=http://127.0.0.1:$SPORT;;
  proto) python3 -I "$HERE/shim_ws.py" "$ROOT" --port $SPORT --upstream $URL 2> "$W/shim.log" & SH=$!
         URL=http://127.0.0.1:$SPORT;;
esac
trap 'kill $SIM ${SH:-} 2>/dev/null' EXIT
sleep 1
cd "$W/proj"
start=$(date +%s%N)
env -i PATH="$PATH" HOME="$W/home" TERM=dumb ANTHROPIC_BASE_URL="$URL" \
  ANTHROPIC_AUTH_TOKEN=dummy ANTHROPIC_MODEL=claude-opus-5.5 DISABLE_TELEMETRY=1 \
  DISABLE_AUTOUPDATER=1 DISABLE_ERROR_REPORTING=1 ENABLE_TOOL_SEARCH=false "$@" \
  timeout 180 "$BIN" -p "$PROMPT" --allowedTools 'WebSearch' \
  --output-format json > "$W/result.json" 2> "$W/stderr.log" || echo "exit $?"
echo "wall_ms $(( ($(date +%s%N) - start) / 1000000 ))" > "$W/wall.txt"
