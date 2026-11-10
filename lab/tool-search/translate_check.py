"""Run captured Claude Code bodies through the shim (strip, default Policy) and then copilot-api's
own translateToOpenAI (lab/copilot-translate/translate.ts via bun). Report what the tool-search
machinery looks like in the OpenAI payload Copilot would receive.

Usage: python3 translate_check.py <repo-root> <copilot-api-dir> <case>=<log.jsonl>... [--json out.json]
"""
import json
import re
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

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")  # OpenAI function-name rule
cases = []
for a in args:
    case, path = a.split("=", 1)
    for line in open(path):
        b = json.loads(line).get("body")
        if isinstance(b, dict) and b.get("tools"):
            cases.append((case, b))

inp = "\n".join(json.dumps(rewrite(b, Policy(), anthropic=True).body) for _, b in cases)
proc = subprocess.run(["bun", f"{root}/lab/copilot-translate/translate.ts",
                       f"{capi}/src/routes/messages/non-stream-translation.ts"],
                      input=inp, capture_output=True, text=True, check=True)
rows = []
for (case, b), line in zip(cases, proc.stdout.splitlines()):
    o = json.loads(line)["ok"]
    tools = o.get("tools") or []
    names = [t["function"]["name"] for t in tools]
    # tool_results whose Anthropic content was only tool_reference blocks
    ref_ids = {blk["tool_use_id"] for m in b["messages"] if m["role"] == "user" and isinstance(m["content"], list)
               for blk in m["content"] if blk.get("type") == "tool_result" and isinstance(blk.get("content"), list)
               and blk["content"] and all(c.get("type") == "tool_reference" for c in blk["content"])}
    referenced = {c["tool_name"] for m in b["messages"] if m["role"] == "user" and isinstance(m["content"], list)
                  for blk in m["content"] if blk.get("type") == "tool_result" and isinstance(blk.get("content"), list)
                  for c in blk["content"] if c.get("type") == "tool_reference"}
    tool_msgs = {m["tool_call_id"]: m["content"] for m in o["messages"] if m["role"] == "tool"}
    deferred_in = [t["name"] for t in b["tools"] if t.get("defer_loading")]
    rows.append({
        "case": case,
        "anthropic_tools": len(b["tools"]),
        "anthropic_deferred": len(deferred_in),
        "openai_tools": len(tools),
        "openai_tool_bytes": len(json.dumps(tools)),
        "deferred_exposed_as_functions": sum(n in names for n in deferred_in),
        "placeholder_exposed": "DeferredToolPlaceholder" in names,
        "defer_loading_key_in_output": "defer_loading" in json.dumps(tools),
        "names_over_64": [n for n in names if not NAME_RE.match(n)],
        "tool_reference_results": len(ref_ids),
        "tool_reference_results_empty_in_openai": sum(tool_msgs.get(i) in ("", None, []) for i in ref_ids),
        "referenced_tools": len(referenced),
        "referenced_tools_in_openai_tools": sum(n in names for n in referenced),
        "referenced_names_in_tool_messages": sum(any(n in str(c) for c in tool_msgs.values()) for n in referenced),
        "schema_keywords": sorted({k for t in tools for k in re.findall(r'"(\$schema|format|default|minimum|propertyNames|exclusiveMinimum|const|anyOf|oneOf)"', json.dumps(t["function"]["parameters"]))}),
    })

for r in rows:
    print(json.dumps(r))
if out_json:
    json.dump(rows, open(out_json, "w"), indent=1)
