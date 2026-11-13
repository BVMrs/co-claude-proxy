#!/usr/bin/env bash
# One headless Claude Code run against capture_server.py through the shim (main), recording every
# request body. The PLAN in the prompt makes the fake model Read each attachment in turn.
# Usage: run_case.sh <claude-bin> <workdir> <prompt> [extra env K=V ...]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
BIN=$1 W=$(realpath -m "$2") PROMPT=$3; shift 3
PORT=${PORT:-4231}; SPORT=$((PORT+1))
rm -rf "$W"; mkdir -p "$W/home" "$W/proj"; : > "$W/requests.jsonl"
(cd "$W/proj" && git init -q)
python3 "$HERE/make_files.py" "$W/proj" > "$W/files.txt"
cat > "$W/home/.claude.json" <<J
{"hasCompletedOnboarding": true, "numStartups": 5,
 "projects": {"$W/proj": {"hasTrustDialogAccepted": true, "hasCompletedProjectOnboarding": true}}}
J
python3 "$HERE/capture_server.py" "$PORT" "$W/requests.jsonl" & SRV=$!
(cd "$ROOT" && exec python3 -m copilot_shim --port $SPORT --upstream http://127.0.0.1:$PORT 2> "$W/shim.log") & SH=$!
trap 'kill $SRV $SH 2>/dev/null' EXIT
sleep 0.8
cd "$W/proj"
env -i PATH="$PATH" HOME="$W/home" TERM=dumb ANTHROPIC_BASE_URL="http://127.0.0.1:$SPORT" \
  ANTHROPIC_AUTH_TOKEN=dummy ANTHROPIC_MODEL=claude-opus-5.5 DISABLE_TELEMETRY=1 \
  DISABLE_AUTOUPDATER=1 DISABLE_ERROR_REPORTING=1 ENABLE_TOOL_SEARCH=false "$@" \
  timeout 180 "$BIN" -p "${PROMPT//@W@/$W/proj}" --allowedTools 'Read' \
  --output-format json > "$W/result.json" 2> "$W/stderr.log" || echo "exit $?"
