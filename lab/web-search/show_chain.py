"""Summarise one run_chain.sh workdir: what reached fake Copilot, what the main loop saw."""
import json
import os
import sys

W = sys.argv[1]
rows = [json.loads(l) for l in open(os.path.join(W, "copilot.jsonl"))]
sub = [r for r in rows if any(t.get("function", {}).get("name") == "web_search" for t in r["openai"].get("tools") or [])]
print(f"copilot_requests={len(rows)} websearch_subrequests_at_copilot={len(sub)}")
for r in sub[:1]:
    print("  translated tool:", json.dumps([t for t in r["openai"]["tools"]]))
    print("  sub-request status:", r["status"])
seen = set()
for r in rows:
    for m in r["openai"]["messages"]:
        if m.get("role") == "tool" and m.get("tool_call_id") not in seen:
            seen.add(m.get("tool_call_id"))
            c = m.get("content")
            c = c if isinstance(c, str) else json.dumps(c)
            print("  main loop tool msg:", c[:400].replace("\n", " | "))
res = json.load(open(os.path.join(W, "result.json")))
print("  final:", res.get("subtype"), "is_error=", res.get("is_error"), "num_turns=", res.get("num_turns"))
log = open(os.path.join(W, "shim.log")).read() if os.path.exists(os.path.join(W, "shim.log")) else ""
print("  shim local answers:", log.count("answered locally"), "| 400s relayed:", log.count("upstream 400"))
print("  " + open(os.path.join(W, "wall.txt")).read().strip())
