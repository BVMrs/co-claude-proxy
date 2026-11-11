#!/usr/bin/env bash
# One headless Claude Code turn against fake_copilot.py, failure config taken from the env
# (FAIL, K, WINDOW, STATUS, FMT, MSG, CODE, RETRY_AFTER; see fake_copilot.py).
# No credentials, no network: throwaway HOME, base URL on 127.0.0.1.
# Usage: run_one.sh <claude-bin> <workdir> <name> [-- extra env K=V ...]
# Writes <workdir>/<name>.jsonl (requests), <name>.out (CC output), <name>.meta (exit, wall seconds).
set -uo pipefail
BIN=$(realpath "$1"); WORK=$(realpath -m "$2"); NAME=$3; shift 3; [ "${1:-}" = "--" ] && shift
EXTRA=("$@")
HERE=$(cd "$(dirname "$0")" && pwd)
PORT=${PORT:-4199}
rm -rf "$WORK/$NAME.home" "$WORK/$NAME.proj"
mkdir -p "$WORK/$NAME.home" "$WORK/$NAME.proj"; LOG="$WORK/$NAME.jsonl"; : > "$LOG"
python3 "$HERE/fake_copilot.py" "$PORT" "$LOG" & SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 0.4
t0=$(date +%s.%N)
( cd "$WORK/$NAME.proj" && env -i PATH="$PATH" HOME="$WORK/$NAME.home" TERM=dumb \
    ANTHROPIC_BASE_URL="http://127.0.0.1:$PORT" ANTHROPIC_AUTH_TOKEN=dummy \
    ANTHROPIC_MODEL=claude-opus-5.5 ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4.5 \
    DISABLE_TELEMETRY=1 DISABLE_AUTOUPDATER=1 DISABLE_ERROR_REPORTING=1 "${EXTRA[@]}" \
    timeout "${TMO:-900}" "$BIN" -p "say ok" < /dev/null > "$WORK/$NAME.out" 2>&1 )
rc=$?
t1=$(date +%s.%N)
echo "{\"exit\": $rc, \"wall\": $(python3 -c "print(round($t1-$t0,2))"), \"t0\": $t0}" > "$WORK/$NAME.meta"
