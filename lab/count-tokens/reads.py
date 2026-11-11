"""Per Read case: file size and lines, lines/chars delivered, the count answer, and any text in the
tool_result that is not a numbered file line (a truncation notice, if Claude Code adds one).
Usage: python3 reads.py <runs dir> <results.json>"""
import json
import pathlib
import re
import sys

runs = pathlib.Path(sys.argv[1])
rows = []
for r in json.load(open(sys.argv[2])):
    if r["kind"] != "read":
        continue
    d = runs / r["id"]
    f = (d / "proj" / "big.txt").read_text(encoding="utf-8")
    last = None
    for line in open(d / "requests.jsonl"):
        rec = json.loads(line)
        for m in (rec.get("body") or {}).get("messages") or []:
            if isinstance(m.get("content"), list):
                for b in m["content"]:
                    if b.get("type") == "tool_result":
                        last = b
    c = last["content"]
    t = c if isinstance(c, str) else "".join(x.get("text", "") for x in c)
    nums = [int(x) for x in re.findall(r"^(\d+)\t", t, re.M)]
    extra = [ln for ln in t.split("\n") if ln and not re.match(r"^\d+\t", ln)]
    ans = r["counts"][0]["answer"] if r["counts"] else None
    rows.append({"id": r["id"], "file_chars": len(f), "file_lines": f.count("\n") + 1,
                 "lines_out": max(nums) if nums else 0, "chars_out": len(t), "count": ans,
                 "notice": extra[:3]})
for x in rows:
    print(f'{x["id"]:30s} file={x["file_chars"]:7d}c/{x["file_lines"]:5d}l out={x["chars_out"]:7d}c/{x["lines_out"]:5d}l '
          f'{"ALL" if x["lines_out"] >= x["file_lines"] - 1 else "CUT"} count={x["count"]} notice={x["notice"]}')
json.dump(rows, open(pathlib.Path(sys.argv[2]).with_name("reads.json"), "w"), indent=1)
