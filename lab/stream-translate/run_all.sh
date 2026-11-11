#!/usr/bin/env bash
# Translate every scenario with copilot-api's stream translator, check it, then play
# each translated stream to the real Claude Code binary.
# Usage: run_all.sh <copilot-api checkout> <claude-bin> <workdir>
set -euo pipefail
API=$(realpath "$1"); BIN=$2; WORK=$3
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
python3 scenarios.py | bun translate_stream.ts "$API/src/routes/messages/stream-translation.ts" > events.json
python3 check.py events.json check_results.json
mkdir -p "$WORK"
for s in $(python3 -c "import json;print(' '.join(json.load(open('events.json'))))"); do
  ./run_one.sh "$BIN" "$WORK" "$s"
done
python3 summarize.py "$WORK" e2e_results.json
