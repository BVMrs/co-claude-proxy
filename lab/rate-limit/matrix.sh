#!/usr/bin/env bash
# Run every scenario of the rate-limit lab in parallel (one fake upstream per port).
# Usage: matrix.sh <claude-bin> <workdir>
# <claude-bin>: the native binary, e.g. node_modules/@anthropic-ai/claude-code-linux-x64/claude
# (node_modules/.bin/claude fails when the postinstall step did not run). Summary: summarize.py <workdir>
set -u
BIN=$1; WORK=$2; HERE=$(cd "$(dirname "$0")" && pwd); mkdir -p "$WORK"
# run <name> <port> [FAKE_ENV=v ...] [-- CC_ENV=v ...]
run() {
  local name=$1 port=$2; shift 2; local fake=() cc=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do fake+=("$1"); shift; done
  [ "${1:-}" = "--" ] && { shift; cc=("$@"); }
  env PORT="$port" "${fake[@]}" "$HERE/run_one.sh" "$BIN" "$WORK" "$name" -- "${cc[@]}" &
}
run k1  4201 FAIL=count K=1
run k3  4202 FAIL=count K=3
run k6  4203 FAIL=count K=6
run A   4204 FAIL=always
run B   4205 FAIL=always RETRY_AFTER=21120
run C   4206 FAIL=always RETRY_AFTER=30
run D   4207 FAIL=window WINDOW=20
run E   4208 FAIL=window WINDOW=60
run F   4209 FAIL=window WINDOW=60 RETRY_AFTER=60
run G   4210 FAIL=always FMT=anthropic
run H   4211 FAIL=always STATUS=503 MSG="upstream unavailable" CODE=service_unavailable
run I   4212 FAIL=always STATUS=500 MSG="internal error" CODE=internal
run J   4213 FAIL=always -- CLAUDE_CODE_MAX_RETRIES=3
run K   4214 FAIL=window WINDOW=180
run M   4215 FAIL=always EXTRA_HDR=x-should-retry:false
run N   4216 FAIL=always STATUS=503 EXTRA_HDR=x-should-retry:false
wait
