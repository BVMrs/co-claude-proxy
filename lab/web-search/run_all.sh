#!/usr/bin/env bash
# Reproduce lab/2026-11-12-web-search.md. Run from the repo root. Needs Claude Code 2.1.321 in
# .agent-work/cc, copilot-api 0.7.0 (0ea08feb) in .agent-work/copilot-api, bun, python3. No network.
set -euo pipefail
BIN=$PWD/.agent-work/cc/node_modules/@anthropic-ai/claude-code-linux-x64/claude
API=$PWD/.agent-work/copilot-api; W=$PWD/.agent-work/web-search; S=lab/web-search
# 1. Claude Code against a bare fake Anthropic endpoint, four sub-request answers.
for m in anthropic text tooluse err400; do
  WS_MODE=$m $S/run_case.sh $BIN $W/$m $W/$m.jsonl 'PLAN: WebSearch {"query": "zork release year"}' ENABLE_TOOL_SEARCH=false </dev/null
  echo "== direct $m"; python3 $S/dump.py $W/$m
done
# 2. Full chain through copilot-api's translators, current shim vs prototype.
for s in main proto; do for m in call callq text reject; do
  COPILOT_WS=$m SHIM=$s $S/run_chain.sh $BIN $API $W/c-$m-$s </dev/null
  echo "== chain copilot=$m shim=$s"; python3 $S/show_chain.py $W/c-$m-$s
done; done
# 3. Workaround without code: deny WebSearch in settings.
DENY=1 COPILOT_WS=call SHIM=main $S/run_chain.sh $BIN $API $W/c-deny </dev/null
echo "== chain deny"; python3 $S/show_chain.py $W/c-deny
