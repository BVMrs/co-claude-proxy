#!/usr/bin/env bash
# Run the e2e matrix in parallel: shim (main | PR | none) x upstream error format, TURNS (default 22) turns each.
# Usage: matrix.sh <claude-bin> <main-checkout> <pr-checkout> <outdir> [<fix-checkout>] [run-id-regex]
# <fix-checkout>: a copy of the PR with copilot_shim/errors.py replaced by errors_fix.py (F-* rows).
set -uo pipefail
BIN=$1; MAIN=$2; PR=$3; OUT=$(realpath -m "$4"); FIX=${5:-}; ONLY=${6:-.}; HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
# id shim errfmt usage badturn
RUNS="
D-copilot     none copilot    delta 0
D-anthropic   none anthropic  delta 0
M-copilot     main copilot    delta 0
M-bad         main copilot    delta 5
P-copilot     pr   copilot    delta 0
P-copilot-nousage pr copilot  none  0
P-commas      pr   commas     delta 0
P-codeonly    pr   codeonly   delta 0
P-openai_ctx  pr   openai_ctx delta 0
P-413         pr   copilot413 delta 0
P-anthropic   pr   anthropic  delta 0
P-bad         pr   copilot    delta 5
F-copilot     fix  copilot    delta 0
F-commas      fix  commas     delta 0
F-codeonly    fix  codeonly   delta 0
F-openai_ctx  fix  openai_ctx delta 0
F-413         fix  copilot413 delta 0
F-anthropic   fix  anthropic  delta 0
F-bad         fix  copilot    delta 5
"
port=4300
while read -r id shim fmt usage bad; do
  [ -z "$id" ] && continue
  echo "$id" | grep -Eq "$ONLY" || continue
  [ "$shim" = fix ] && [ -z "$FIX" ] && continue
  case $shim in main) src=$MAIN;; pr) src=$PR;; fix) src=$FIX;; *) src=none;; esac
  echo "{\"id\":\"$id\",\"shim\":\"$shim\",\"errfmt\":\"$fmt\",\"usage\":\"$usage\",\"badturn\":$bad}" > "$OUT/$id.cfg"
  PORT=$port SHIM_SRC=$src CAP=128000 ERRFMT=$fmt USAGE=$usage BADTURN=$bad \
    "$HERE/run.sh" "$BIN" "$OUT/$id" "$OUT/$id.jsonl" "${TURNS:-22}" > "$OUT/$id.runlog" 2>&1 &
  port=$((port + 2))
done <<< "$RUNS"
wait
for c in "$OUT"/*.cfg; do id=$(basename "$c" .cfg); echo "$id" | grep -Eq "$ONLY" || continue
  echo "$(cat "$c") $(python3 "$HERE/summarize.py" "$OUT/$id.jsonl" --json)"; done
