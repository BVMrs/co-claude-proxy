#!/usr/bin/env bash
# Drive a real Claude Code binary for TURNS headless turns (`-p`, then `--continue`) against
# fake_copilot.py; each prompt carries one <<PAD>> marker (= PAD virtual tokens).
# No credentials, no network: HOME is a throwaway dir, base URL is 127.0.0.1.
# Usage: CAP=128000 ERRFMT=copilot USAGE=delta lab/compact-cap/run.sh <claude-bin> <workdir> <log.jsonl> [TURNS] [-- extra env K=V ...]
set -euo pipefail
BIN=$(realpath "$1"); WORK=$(realpath -m "$2"); OUT=$(realpath -m "$3"); TURNS=${4:-20}
shift 4 || true; [ "${1:-}" = "--" ] && shift
EXTRA=("$@")
HERE=$(cd "$(dirname "$0")" && pwd)
PORT=${PORT:-4198}
MODEL=${MODEL:-claude-opus-5.5}
rm -rf "$WORK"; mkdir -p "$WORK/home" "$WORK/proj"; : > "$OUT"
python3 "$HERE/fake_copilot.py" "$PORT" "$OUT" & SRV=$!
trap 'kill $SRV' EXIT
sleep 0.5
for i in $(seq 1 "$TURNS"); do
  cont=(); [ "$i" -gt 1 ] && cont=(--continue)
  echo "{\"turn\": $i}" >> "$OUT"
  ( cd "$WORK/proj" && env -i PATH="$PATH" HOME="$WORK/home" TERM=dumb \
      ANTHROPIC_BASE_URL="http://127.0.0.1:$PORT" ANTHROPIC_AUTH_TOKEN=dummy \
      ANTHROPIC_MODEL="$MODEL" ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4.5 \
      DISABLE_TELEMETRY=1 DISABLE_AUTOUPDATER=1 DISABLE_ERROR_REPORTING=1 "${EXTRA[@]}" \
      timeout 120 "$BIN" -p "${cont[@]}" "turn $i <<PAD>> reply ok" < /dev/null \
      > "$WORK/turn$i.out" 2>&1 ) || echo "{\"turn\": $i, \"exit\": $?}" >> "$OUT"
  echo "{\"turn\": $i, \"stdout\": $(head -c 400 "$WORK/turn$i.out" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')}" >> "$OUT"
done
