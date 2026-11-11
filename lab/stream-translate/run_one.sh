#!/usr/bin/env bash
# One headless Claude Code turn whose main reply is scenario $3 from events.json.
# No credentials, no network: throwaway HOME, base URL on 127.0.0.1.
# Usage: run_one.sh <claude-bin> <workdir> <scenario>
set -uo pipefail
BIN=$(realpath "$1"); WORK=$(realpath -m "$2"); NAME=$3
HERE=$(cd "$(dirname "$0")" && pwd)
PORT=${PORT:-4198}
rm -rf "$WORK/$NAME.home" "$WORK/$NAME.proj"; mkdir -p "$WORK/$NAME.home" "$WORK/$NAME.proj"
LOG="$WORK/$NAME.jsonl"; : > "$LOG"
SCENARIO=$NAME EVENTS="${EVENTS:-$HERE/events.json}" python3 "$HERE/fake_upstream.py" "$PORT" "$LOG" & SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 0.4
t0=$(date +%s.%N)
( cd "$WORK/$NAME.proj" && env -i PATH="$PATH" HOME="$WORK/$NAME.home" TERM=dumb \
    ANTHROPIC_BASE_URL="http://127.0.0.1:$PORT" ANTHROPIC_AUTH_TOKEN=dummy \
    ANTHROPIC_MODEL=claude-opus-5.5 ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4.5 \
    DISABLE_TELEMETRY=1 DISABLE_AUTOUPDATER=1 DISABLE_ERROR_REPORTING=1 \
    timeout "${TMO:-300}" "$BIN" -p "Run echo MARK_A and echo MARK_B with Bash." \
      --allowedTools Bash --output-format json < /dev/null > "$WORK/$NAME.out" 2> "$WORK/$NAME.err" )
rc=$?
t1=$(date +%s.%N)
echo "{\"exit\": $rc, \"wall\": $(python3 -c "print(round($t1-$t0,2))")}" > "$WORK/$NAME.meta"
