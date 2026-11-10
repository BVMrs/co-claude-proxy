#!/usr/bin/env bash
# Run one headless Claude Code request sequence against capture_server.py with the lab MCP server.
# Usage: run_case.sh <claude-bin> <workdir> <out.jsonl> <n_mcp_tools> <prompt> [extra env KEY=VAL ...]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=$1 W=$2 OUT=$3 N=$4 PROMPT=$5; shift 5
PORT=${PORT:-4198}
rm -rf "$W"; mkdir -p "$W/home" "$W/proj"; : > "$OUT"
(cd "$W/proj" && git init -q)
cat > "$W/home/.claude.json" <<J
{"hasCompletedOnboarding": true, "numStartups": 5,
 "projects": {"$W/proj": {"hasTrustDialogAccepted": true, "hasCompletedProjectOnboarding": true}}}
J
cat > "$W/mcp.json" <<J
{"mcpServers": {"lab": {"type": "stdio", "command": "python3", "args": ["-I", "$HERE/mcp_server.py", "$N"]}}}
J
python3 "$HERE/capture_server.py" "$PORT" "$OUT" & SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 0.5
cd "$W/proj"
env -i PATH="$PATH" HOME="$W/home" TERM=dumb ANTHROPIC_BASE_URL="http://127.0.0.1:$PORT" \
  ANTHROPIC_AUTH_TOKEN=dummy ANTHROPIC_MODEL=claude-opus-5.5 DISABLE_TELEMETRY=1 \
  DISABLE_AUTOUPDATER=1 DISABLE_ERROR_REPORTING=1 "$@" \
  timeout 120 "$BIN" -p "$PROMPT" --mcp-config "$W/mcp.json" --strict-mcp-config \
  --allowedTools 'mcp__lab__*' --output-format json > "$W/result.json" 2> "$W/stderr.log" || echo "exit $?"
