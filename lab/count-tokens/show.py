"""Print the last tool_result of given run ids (head and tail of each text block).
Usage: python3 show.py <runs dir> <id> [<id> ...]"""
import json
import pathlib
import sys

runs = pathlib.Path(sys.argv[1])
for cid in sys.argv[2:]:
    last = None
    for line in open(runs / cid / "requests.jsonl"):
        r = json.loads(line)
        for m in (r.get("body") or {}).get("messages") or []:
            if isinstance(m.get("content"), list):
                for b in m["content"]:
                    if b.get("type") == "tool_result":
                        last = b
    c = last["content"]
    blocks = [{"type": "text", "text": c}] if isinstance(c, str) else c
    print("=====", cid, [(x["type"], len(x.get("text", "")) if x["type"] == "text" else len(json.dumps(x)))
                         for x in blocks])
    for x in blocks:
        if x["type"] == "text":
            t = x["text"]
            print(repr(t[:300]))
            print("...", repr(t[-500:]))
