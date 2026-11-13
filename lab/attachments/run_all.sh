#!/usr/bin/env bash
# Reproduce lab/2026-11-12-attachments.md from the repo root. Needs Claude Code 2.1.321 in .agent-work/cc,
# copilot-api 0.7.0 (0ea08feb) in .agent-work/copilot-api, bun, python3. No network, no login.
set -euo pipefail
BIN=$PWD/.agent-work/cc/node_modules/@anthropic-ai/claude-code-linux-x64/claude
A=.agent-work/attachments; S=lab/attachments
$S/run_case.sh $BIN $A/read 'PLAN: Read {"file_path": "@W@/small.png"} ;; Read {"file_path": "@W@/screen.png"} ;; Read {"file_path": "@W@/spec.pdf"} ;; Read {"file_path": "@W@/plot.ipynb"}' </dev/null
python3 $S/shapes.py $A/read/requests.jsonl --dump $A/read/last.json
$S/run_case.sh $BIN $A/mention 'Describe @small.png and summarise @spec.pdf' </dev/null
python3 $S/shapes.py $A/mention/requests.jsonl --dump $A/mention/last.json
# MCP screenshots: body captured by lab/count-tokens (run that first)
python3 $S/shapes.py .agent-work/count-tokens/runs/mcp-40000-3x200-none/requests.jsonl --dump $A/mcp.json
python3 - <<'PY'
import json, copy
b = json.load(open('.agent-work/attachments/read/last.json')); m = b['messages']
par = copy.deepcopy(b); par['messages'] = m[:1] + [{'role': 'assistant', 'content': m[1]['content'] + m[3]['content']},
    {'role': 'user', 'content': m[2]['content'] + m[4]['content'] + [{'type': 'text', 'text': 'now compare them'}]}]
json.dump(par, open('.agent-work/attachments/parallel.json', 'w'))
PY
python3 $S/check.py .agent-work/copilot-api $A/read/last.json $A/mention/last.json $A/mcp.json $A/parallel.json
