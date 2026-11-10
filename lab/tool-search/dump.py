"""Print a compact view of each captured Messages request. Usage: dump.py <log.jsonl>"""
import json
import sys

for line in open(sys.argv[1]):
    r = json.loads(line)
    b = r.get("body")
    if not isinstance(b, dict) or "messages" not in b:
        continue
    tools = b.get("tools") or []
    names = [t["name"] for t in tools]
    print(f"{b.get('model')} ntools={len(tools)} deferred={sum(bool(t.get('defer_loading')) for t in tools)} "
          f"maxname={max(map(len, names), default=0)} toolbytes={len(json.dumps(tools))} nmsg={len(b['messages'])}")
    print("   non-deferred mcp:", sum(1 for t in tools if t["name"].startswith("mcp__") and not t.get("defer_loading")),
          "deferred names:", [n for t, n in zip(tools, names) if t.get("defer_loading")][:6])
    print("   last:", json.dumps(b["messages"][-1]["content"])[:400])
