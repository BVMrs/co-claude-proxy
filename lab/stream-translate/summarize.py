"""Summarize Claude Code end-to-end runs (run_one.sh output) per scenario.

For each scenario: exit code, wall time, how many times the scenario reply was
requested (retries), which tool calls Claude Code executed (tool_result ids and
whether they were errors), what it replayed as the assistant turn, and the final
result / error text it printed.
Usage: python3 summarize.py <workdir> [out.json]
"""
import glob
import json
import os
import sys

work = sys.argv[1]
rows = {}
for meta in sorted(glob.glob(os.path.join(work, "*.meta"))):
    name = os.path.basename(meta)[:-5]
    m = json.load(open(meta))
    reqs = [json.loads(line) for line in open(os.path.join(work, name + ".jsonl")) if line.strip()]
    served = sum(r["served"] == "scenario" for r in reqs)
    follow = [r for r in reqs if r["tool_results"] or (r["served"] == "done" and r["n_tools"] > 3)]
    executed, replayed = [], []
    for r in follow:
        for t in r["tool_results"]:
            executed.append({"id": t["id"], "is_error": t["is_error"], "content": t["content"][:110]})
        if r["assistant_replayed"] and not replayed:
            replayed = [(b["type"], b["id"] or b["text"][:30], b["input"]) for b in r["assistant_replayed"]]
    out_raw = open(os.path.join(work, name + ".out")).read()
    try:
        out = json.loads(out_raw)
        result = {"is_error": out.get("is_error"), "subtype": out.get("subtype"),
                  "stop_reason": out.get("stop_reason"), "result": str(out.get("result"))[:200],
                  "num_turns": out.get("num_turns")}
    except ValueError:
        result = {"raw": out_raw[:300]}
    err = open(os.path.join(work, name + ".err")).read().strip()[:300]
    rows[name] = {"exit": m["exit"], "wall": m["wall"], "requests": len(reqs), "scenario_served": served,
                  "executed": executed, "replayed": replayed, "result": result, "stderr": err}
    print(f"== {name}: exit={m['exit']} wall={m['wall']}s requests={len(reqs)} scenario_served={served}")
    print("   executed:", [(e["id"], "ERR" if e["is_error"] else "ok", e["content"][:70]) for e in executed])
    print("   replayed:", replayed)
    print("   result:", result)
    if err:
        print("   stderr:", err)
if len(sys.argv) > 2:
    json.dump(rows, open(sys.argv[2], "w"), indent=1)
