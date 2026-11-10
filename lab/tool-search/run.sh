#!/usr/bin/env bash
# Reproduce lab/2026-11-10-tool-search.md. Run from the repo root. Needs Claude Code 2.1.321 in
# .agent-work/cc and copilot-api 0.7.0 (0ea08feb) in .agent-work/copilot-api, plus bun. No network.
set -euo pipefail
BIN=$PWD/.agent-work/cc/node_modules/@anthropic-ai/claude-code-linux-x64/claude
W=$PWD/.agent-work/tool-search; S=lab/tool-search
L80=mcp__lab__long_$(printf 'z%.0s' $(seq 65))
$S/run_case.sh $BIN $W/t1 $W/t1.jsonl 40 'PLAN: ToolSearch {"query": "select:mcp__lab__lab_tool_03", "max_results": 5} ;; mcp__lab__lab_tool_03 {"arg": "hi"}' </dev/null
$S/run_case.sh $BIN $W/t2 $W/t2.jsonl 40 'PLAN: ToolSearch {"query": "lab tool", "max_results": 5} ;; mcp__lab__lab_tool_01 {"arg": "hi"} ;; ToolSearch {"query": "select:WebFetch,'$L80'", "max_results": 5} ;; '$L80' {"arg": "z"}' </dev/null
$S/run_case.sh $BIN $W/t3 $W/t3.jsonl 40 'PLAN: mcp__lab__lab_tool_01 {"arg": "hi"} ;; '$L80' {"arg": "z"}' ENABLE_TOOL_SEARCH=false </dev/null
$S/run_case.sh $BIN $W/t4 $W/t4.jsonl 150 'PLAN: mcp__lab__lab_tool_149 {"arg": "hi"}' ENABLE_TOOL_SEARCH=false </dev/null
$S/run_case.sh $BIN $W/t5 $W/t5.jsonl 150 'PLAN: ToolSearch {"query": "select:mcp__lab__lab_tool_149", "max_results": 5} ;; mcp__lab__lab_tool_149 {"arg": "hi"}' </dev/null
for t in t1 t2 t3 t4 t5; do echo "== $t"; python3 $S/dump.py $W/$t.jsonl; done
python3 $S/translate_check.py $PWD $PWD/.agent-work/copilot-api select40=$W/t1.jsonl keyword40=$W/t2.jsonl \
  off40=$W/t3.jsonl off150=$W/t4.jsonl select150=$W/t5.jsonl --json $S/translate_results.json
