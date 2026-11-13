"""Summarise the content-block shapes in the last /v1/messages body of a capture (requests.jsonl).
Usage: python3 shapes.py <requests.jsonl> [--dump out.json]   (--dump writes the last main-loop body)"""
import json
import sys


def shape(b):
    t = b.get("type")
    if t == "text":
        return f"text({len(b['text'])})"
    if t in ("image", "document"):
        s = b.get("source", {})
        return f"{t}[{s.get('type')},{s.get('media_type')},{len(s.get('data', '') or '')}]"
    if t == "tool_result":
        c = b.get("content")
        inner = [shape(x) for x in c] if isinstance(c, list) else [f"str({len(c or '')})"]
        return f"tool_result({', '.join(inner)})"
    return t


bodies = [r["body"] for r in map(json.loads, open(sys.argv[1]))
          if r.get("method") == "POST" and "/messages" in r.get("path", "") and "count_tokens" not in r["path"]
          and isinstance(r.get("body"), dict) and r["body"].get("tools")]
last = bodies[-1]
print("main-loop requests:", len(bodies))
for i, m in enumerate(last["messages"]):
    c = m["content"]
    print(i, m["role"], [shape(b) for b in c] if isinstance(c, list) else f"str({len(c)})")
if "--dump" in sys.argv:
    json.dump(last, open(sys.argv[sys.argv.index("--dump") + 1], "w"))
