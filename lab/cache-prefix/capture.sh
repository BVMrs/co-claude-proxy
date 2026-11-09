#!/usr/bin/env bash
# Record the request bodies a real Claude Code binary sends over a 6-turn session (two
# Read tool calls, one Bash `cd` that changes the working directory), against
# capture_server.py. No credentials, no network:
# HOME is a throwaway dir and ANTHROPIC_BASE_URL points at 127.0.0.1.
# Usage: lab/cache-prefix/capture.sh <claude-binary> <workdir> <out.jsonl>
set -euo pipefail
BIN=$(realpath "$1"); WORK=$(realpath -m "$2"); OUT=$(realpath -m "$3")
HERE=$(cd "$(dirname "$0")" && pwd)
PORT=${PORT:-4199}
rm -rf "$WORK"; mkdir -p "$WORK/home" "$WORK/proj"; : > "$OUT"
mkdir -p "$WORK/proj/sub"; printf 'alpha\nbeta\ngamma\n' > "$WORK/proj/notes.txt"
(cd "$WORK/proj" && git init -q && git add . && git -c user.email=x@x -c user.name=x commit -qm init)
python3 "$HERE/capture_server.py" "$PORT" "$OUT" & SRV=$!
trap 'kill $SRV' EXIT
sleep 0.5
run() {
  (cd "$WORK/proj" && env -i PATH="$PATH" HOME="$WORK/home" TERM=dumb \
     ANTHROPIC_BASE_URL="http://127.0.0.1:$PORT" ANTHROPIC_AUTH_TOKEN=dummy \
     ANTHROPIC_MODEL=claude-opus-5.5 ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4.5 \
     DISABLE_TELEMETRY=1 DISABLE_AUTOUPDATER=1 DISABLE_ERROR_REPORTING=1 \
     timeout 120 "$BIN" -p "$@" --allowedTools Read Bash < /dev/null ) || echo "claude exit $?" >&2
}
run "READ_FILE:$WORK/proj/notes.txt and tell me what it says"
run --continue "now say hello"
run --continue "READ_FILE:$WORK/proj/notes.txt again please"
run --continue "BASH:cd $WORK/proj/sub
please"
run --continue "thanks"
run --continue "and one more"
wc -l "$OUT"
