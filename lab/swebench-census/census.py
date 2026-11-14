"""Census of SWE-bench Lite problem statements vs the shim. Usage: census.py file.parquet"""
import sys, json, re, statistics as st, collections, hashlib
sys.path.insert(0, ".")
import pyarrow.parquet as pq
from copilot_shim.rewrite import rewrite, Policy
from copilot_shim.errors import PROMPT_LIMIT_PHRASE_RE

t = pq.read_table(sys.argv[1]).to_pylist()
print("rows", len(t), "sha256", hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest()[:16])
print("cols", list(t[0]))
ps = [r["problem_statement"] for r in t]
def q(v, p): v = sorted(v); return v[min(len(v)-1, int(p*len(v)))]
ch = [len(s) for s in ps]
print("chars min/med/p90/p99/max", min(ch), int(st.median(ch)), q(ch,.9), q(ch,.99), max(ch))
tok = [len(s.encode())/3.5 for s in ps]
print("approx tokens (bytes/3.5) med/max", int(st.median(tok)), int(max(tok)))
print("repos", collections.Counter(r["repo"] for r in t).most_common())
feat = {
 "crlf": lambda s: "\r" in s,
 "nul": lambda s: "\x00" in s,
 "lone_surrogate": lambda s: bool(re.search("[\ud800-\udfff]", s)),
 "non_ascii": lambda s: any(ord(c) > 127 for c in s),
 "astral(emoji)": lambda s: any(ord(c) > 0xFFFF for c in s),
 "ctrl_chars(other)": lambda s: bool(re.search("[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", s)),
 "trailing_ws": lambda s: s != s.rstrip(),
 "leading_ws": lambda s: s != s.lstrip(),
 "empty": lambda s: not s.strip(),
 "md_image": lambda s: bool(re.search(r"!\[[^\]]*\]\([^)]+\)|<img ", s)),
 "data_uri": lambda s: "data:image" in s,
 "limit_phrase": lambda s: bool(PROMPT_LIMIT_PHRASE_RE.search(s)),
 "claude_tags": lambda s: bool(re.search(r"</?(system-reminder|function_calls|antml|thinking)", s)),
 "line>2000": lambda s: any(len(l) > 2000 for l in s.split("\n")),
}
for k, f in feat.items():
    print(f"{k:18}", sum(1 for s in ps if f(s)))
# push through rewrite in both shapes, check JSON round trip and invariants
bad = 0
for r in t:
    s = r["problem_statement"]
    for shape in ("string", "blocks"):
        content = s if shape == "string" else [{"type": "text", "text": s}]
        body = {"model": "claude-opus-4.5", "messages": [{"role": "user", "content": content}, {"role": "assistant", "content": "{"}]}
        for mode in ("strip", "hint"):
            res = rewrite(body, Policy(mode=mode), anthropic=True)
            try:
                raw = json.dumps(res.body).encode()
                back = json.loads(raw)
                ok = back == res.body and res.body["messages"][0]["role"] == "user"
            except Exception as e:
                ok = False
            bad += not ok
print("rewrite round-trip failures", bad, "of", len(t)*4)
# patch / test_patch sizes (what Claude Code would produce / read)
pc = [len(r["patch"]) for r in t]
print("gold patch chars med/max", int(st.median(pc)), max(pc))
print("files in gold patch med/max", int(st.median([r["patch"].count("diff --git") for r in t])), max(r["patch"].count("diff --git") for r in t))
# smallest 8 by problem length per repo (pick candidates)
small = sorted(t, key=lambda r: len(r["problem_statement"]))
print("shortest5", [(r["instance_id"], len(r["problem_statement"])) for r in small[:5]])
print("longest5", [(r["instance_id"], len(r["problem_statement"])) for r in small[-5:]])
for k in ("version", "environment_setup_commit"): pass
