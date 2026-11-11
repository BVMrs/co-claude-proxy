"""Summarize a matrix.sh run: per scenario, requests, failures, retry gaps, outcome, wall time.

Usage: python3 summarize.py <workdir> [name ...]
"""
import json
import os
import sys

work = sys.argv[1]
names = sys.argv[2:] or sorted(f[:-5] for f in os.listdir(work) if f.endswith(".meta"))
print(f"{'cfg':4} {'reqs':>4} {'fail':>4} {'exit':>4} {'wall_s':>7}  gaps between requests (s)  | output")
for name in names:
    reqs = [json.loads(line) for line in open(os.path.join(work, name + ".jsonl")) if line.strip()]
    meta = json.load(open(os.path.join(work, name + ".meta")))
    out = open(os.path.join(work, name + ".out"), errors="replace").read().strip().replace("\n", " ")
    ts = [r["t"] for r in reqs]
    gaps = [round(b - a, 1) for a, b in zip(ts, ts[1:])]
    fails = sum(r["status"] != 200 for r in reqs)
    models = sorted({r["model"] for r in reqs})
    print(f"{name:4} {len(reqs):4} {fails:4} {meta['exit']:4} {meta['wall']:7}  {gaps} {models} | {out[:220]}")
