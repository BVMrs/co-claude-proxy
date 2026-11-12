"""Requests and virtual prompt tokens sent per run (main loop + compaction calls).
Usage: python3 cost.py <runs-dir> <id>..."""
import json
import sys

for r in sys.argv[2:]:
    q = [json.loads(line) for line in open(f"{sys.argv[1]}/{r}.jsonl")]
    q = [x for x in q if x.get("path", "").endswith("messages")]
    tot = sum(x["virtual"] for x in q) / 1e6
    comp = sum(x["virtual"] for x in q if x["compact"]) / 1e6
    print(f"{r:20} requests={len(q):4} tokens_sent={tot:6.2f}M compaction_tokens={comp:5.2f}M")
