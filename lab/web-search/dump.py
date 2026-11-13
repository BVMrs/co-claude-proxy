"""Summarise one capture: request count, web-search sub-requests, tool_results the main loop saw, final result."""
import json
import os
import sys

W = sys.argv[1]
reqs = [json.loads(l) for l in open(W + ".jsonl")]
posts = [r for r in reqs if r.get("method") == "POST" and isinstance(r.get("body"), dict)]
sub = [r for r in posts if any("web_search" in str(t.get("type", "")) for t in r["body"].get("tools") or [])]
print(f"requests={len(posts)} websearch_subrequests={len(sub)}")
seen = set()
for r in posts:
    for m in r["body"]["messages"]:
        c = m.get("content")
        for b in c if isinstance(c, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") not in seen:
                seen.add(b.get("tool_use_id"))
                t = b.get("content")
                t = t if isinstance(t, str) else json.dumps(t)
                print("  tool_result is_error=%s: %s" % (b.get("is_error"), t[:400].replace("\n", " | ")))
try:
    res = json.load(open(os.path.join(W, "result.json")))
    print("  final:", res.get("subtype"), "is_error=", res.get("is_error"), repr(res.get("result"))[:200])
except Exception as e:
    print("  result:", e)
print("  " + open(os.path.join(W, "wall.txt")).read().strip())
