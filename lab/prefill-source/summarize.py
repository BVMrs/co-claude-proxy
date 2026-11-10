"""Classify every request in capture logs and run the shim's rewrite() on it.

For each request: kind (main / subagent / helper), model, tools, the role sequence tail,
whether it ends on assistant (prefill) as sent, whether it ends on assistant after the
system-message fold, and what the shim (default Policy, mode strip) does.
Prints no prompt text except a 60-char helper-system prefix (Claude Code's own text).
Usage: python3 summarize.py <repo-root> <log.jsonl>... [--json out.json]
"""
import json
import sys

root = sys.argv[1]
sys.path.insert(0, root)
from copilot_shim.rewrite import Policy, rewrite  # noqa: E402

args = sys.argv[2:]
out_json = None
if "--json" in args:
    i = args.index("--json")
    out_json = args[i + 1]
    args = args[:i] + args[i + 2:]


def sys_text(b):
    s = b.get("system")
    if isinstance(s, str):
        return s
    return " ".join(x.get("text", "") for x in s or [] if isinstance(x, dict))


def kind(b):
    tools = [t.get("name") for t in b.get("tools") or []]
    msgs = b.get("messages") or []
    first = msgs[0].get("content") if msgs else ""
    first = first if isinstance(first, str) else json.dumps(first)
    if "AGENTINNER" in first:
        return "subagent"
    if tools:
        return "main"
    return "helper"


def helper_label(b):
    s = sys_text(b)
    s = s.split("\n", 1)[1] if s.startswith("x-anthropic-billing-header") else s
    return " ".join(s.split())[:60]


rows = []
for path in args:
    step = None
    for line in open(path):
        r = json.loads(line)
        if "step" in r:
            step = r["step"][:40]
            continue
        b = r.get("body")
        if not isinstance(b, dict) or "messages" not in b:
            continue
        msgs = b["messages"]
        roles = [m.get("role") for m in msgs]
        res = rewrite(b, Policy(), anthropic=True)
        after = [m.get("role") for m in res.body["messages"]]
        last = msgs[-1]
        rows.append({
            "log": path.rsplit("/", 1)[-1], "step": step, "path": r["path"].split("?")[0],
            "kind": kind(b), "model": b.get("model"), "n_tools": len(b.get("tools") or []),
            "roles_tail": roles[-4:], "ends_assistant_raw": roles[-1] == "assistant",
            "ends_assistant_after_fold": bool(after) and after[-1] == "assistant",
            "trailing_assistant_blocks": ([x.get("type") for x in last["content"]]
                                          if roles[-1] == "assistant" and isinstance(last.get("content"), list)
                                          else None),
            "consecutive_same_role": any(a == c for a, c in zip(after, after[1:])),
            "shim_action": res.action, "system_folded": res.system_folded,
            "helper": helper_label(b) if kind(b) == "helper" else None,
            "thinking": (b.get("thinking") or {}).get("type"), "stream": bool(b.get("stream")),
        })

for x in rows:
    print(f"{x['log']:<22} {x['kind']:<8} {str(x['model']):<28} tools={x['n_tools']:<2} "
          f"tail={','.join(r[0] for r in x['roles_tail'])} raw_asst={int(x['ends_assistant_raw'])} "
          f"asst_after_fold={int(x['ends_assistant_after_fold'])} shim={x['shim_action']} "
          f"fold={x['system_folded']} step={x['step']!r}" + (f" helper={x['helper']!r}" if x['helper'] else ""))

summary = {}
for x in rows:
    k = (x["log"], x["kind"], x["model"])
    s = summary.setdefault(" | ".join(map(str, k)), {"n": 0, "raw_prefill": 0, "after_fold_prefill": 0, "stripped": 0})
    s["n"] += 1
    s["raw_prefill"] += x["ends_assistant_raw"]
    s["after_fold_prefill"] += x["ends_assistant_after_fold"]
    s["stripped"] += x["shim_action"] != "none"
print(json.dumps(summary, indent=1))
if out_json:
    json.dump({"rows": rows, "summary": summary}, open(out_json, "w"), indent=1)
