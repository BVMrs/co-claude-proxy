"""Run every captured request through the shim (off / strip) and then through copilot-api's
own translateToOpenAI (lab/copilot-translate/translate.ts, bun), and report the role of
the last OpenAI message, i.e. what Copilot's prefill check sees.
Usage: python3 translate_check.py <repo-root> <copilot-api-dir> <log.jsonl>... [--json out.json]
"""
import json
import subprocess
import sys

root, capi = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
from copilot_shim.rewrite import Policy, rewrite  # noqa: E402

args = sys.argv[3:]
out_json = None
if "--json" in args:
    i = args.index("--json")
    out_json, args = args[i + 1], args[:i] + args[i + 2:]

bodies = []
for path in args:
    for line in open(path):
        r = json.loads(line)
        b = r.get("body")
        if isinstance(b, dict) and "messages" in b and "count_tokens" not in r["path"]:
            kind = "main" if b.get("tools") else "helper"
            first = b["messages"][0].get("content")
            if "AGENTINNER" in json.dumps(first):
                kind = "subagent"
            bodies.append((path.rsplit("/", 1)[-1], kind, b))

result = {}
for mode in ("off", "strip"):
    inp = "\n".join(json.dumps(rewrite(b, Policy(mode=mode), anthropic=True).body) for _, _, b in bodies)
    proc = subprocess.run(["bun", f"{root}/lab/copilot-translate/translate.ts",
                           f"{capi}/src/routes/messages/non-stream-translation.ts"],
                          input=inp, capture_output=True, text=True, check=True)
    outs = [json.loads(x) for x in proc.stdout.splitlines()]
    for (log, kind, _), o in zip(bodies, outs):
        msgs = [m for m in o["ok"]["messages"] if m["role"] != "system"]
        last = msgs[-1]["role"] if msgs else None
        key = f"{log} | {kind}"
        s = result.setdefault(key, {}).setdefault(mode, {"n": 0, "ends_assistant": 0})
        s["n"] += 1
        s["ends_assistant"] += last == "assistant"

print(json.dumps(result, indent=1))
tot = {m: sum(v[m]["ends_assistant"] for v in result.values()) for m in ("off", "strip")}
print("total requests", len(bodies), "ends_assistant", tot)
if out_json:
    json.dump({"by_log_kind": result, "total": len(bodies), "ends_assistant": tot}, open(out_json, "w"), indent=1)
