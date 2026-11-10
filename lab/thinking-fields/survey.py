"""Which request-level controls in real Claude Code bodies survive copilot-api's translation?

Usage: python3 lab/thinking-fields/survey.py <copilot-api checkout> <capture.jsonl>...

Reads captured Anthropic Messages bodies (records with a "body" key), runs
copilot_shim.rewrite (default strip policy, Anthropic shape) and then copilot-api's own
translateToOpenAI (lab/copilot-translate/translate.ts under bun). For each top-level
field it reports whether the value reaches the OpenAI payload that Copilot sees.
No network, no Copilot calls. Never prints metadata.user_id values (they hold the
capturing machine's device_id).
"""
import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from copilot_shim.rewrite import Policy, rewrite  # noqa: E402

# Where copilot-api 0.7.0's translateToOpenAI puts each Anthropic field (None = nowhere).
OPENAI_TARGET = {
    "model": "model", "max_tokens": "max_tokens", "stream": "stream",
    "temperature": "temperature", "top_p": "top_p", "stop_sequences": "stop",
    "metadata": "user", "tools": "tools", "tool_choice": "tool_choice",
    "system": "messages", "messages": "messages",
}
CONTROL_KEYS = ("reasoning_effort", "thinking_budget", "response_format",
                "thinking", "output_config", "context_management")


def load(paths):
    out = []
    for p in paths:
        for line in open(p):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            b = r.get("body") if isinstance(r, dict) else None
            if isinstance(b, dict) and "messages" in b and "count_tokens" not in r.get("path", ""):
                out.append((os.path.basename(os.path.dirname(os.path.abspath(p))), b))
    return out


def klass(b):
    if (b.get("output_config") or {}).get("format"):
        return "json_schema helper"
    th = (b.get("thinking") or {}).get("type")
    return f"loop (thinking={th})"


def main():
    capi, paths = sys.argv[1], sys.argv[2:]
    reqs = load(paths)
    policy = Policy()
    rewritten = [rewrite(b, policy, anthropic=True).body for _, b in reqs]
    proc = subprocess.run(
        ["bun", os.path.join(ROOT, "lab/copilot-translate/translate.ts"),
         os.path.join(os.path.abspath(capi), "src/routes/messages/non-stream-translation.ts")],
        input="\n".join(json.dumps(b) for b in rewritten), capture_output=True, text=True, check=True)
    outs = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert len(outs) == len(rewritten), (len(outs), len(rewritten), proc.stderr[-500:])

    fields, reached = collections.Counter(), collections.Counter()
    values = collections.defaultdict(collections.Counter)
    classes, other = collections.Counter(), collections.Counter()
    user_len = []
    for (src, orig), body, o in zip(reqs, rewritten, outs):
        oa = o.get("ok")
        if oa is None:
            other["translate_error"] += 1
            continue
        classes[klass(orig)] += 1
        for k, v in body.items():
            fields[k] += 1
            if k not in ("messages", "system", "tools", "metadata"):
                values[k][json.dumps(v, sort_keys=True)[:140]] += 1
            tgt = OPENAI_TARGET.get(k)
            if tgt and oa.get(tgt) is not None:
                reached[k] += 1
        for k in CONTROL_KEYS:
            other["openai_payload_has_" + k] += k in oa
        if oa.get("user"):
            user_len.append(len(oa["user"]))
            other["openai_user_contains_device_id"] += "device_id" in oa["user"]
            other["openai_user_contains_session_id"] += "session_id" in oa["user"]
        if "max_tokens" in orig:
            other["max_tokens_forwarded_verbatim"] += oa.get("max_tokens") == orig["max_tokens"]
        if "temperature" in orig:
            other["temperature_forwarded"] += oa.get("temperature") == orig["temperature"]
        for m in body.get("messages", []):
            if m.get("role") == "assistant" and isinstance(m.get("content"), list):
                for blk in m["content"]:
                    if blk.get("type") in ("thinking", "redacted_thinking"):
                        other["history_thinking_blocks"] += 1
                        other["history_thinking_with_signature"] += bool(blk.get("signature"))
                        other["history_thinking_text_chars"] += len(blk.get("thinking") or "")
        if (orig.get("output_config") or {}).get("format"):
            other["json_schema_requests"] += 1
            other["json_schema_reaching_copilot"] += "response_format" in oa
    # Routing under copilot-api PR #280 (6d124a7), mirrored from
    # src/routes/messages/native-handler.ts shouldUseNativeMessages():
    #   model.startsWith("claude") && messages.at(-1)?.role !== "assistant"
    # Checked on the raw Claude Code body (copilot-api alone) and on the shim's output.
    route = collections.Counter()
    for (_, orig), body in zip(reqs, rewritten):
        for tag, b in (("raw", orig), ("shim", body)):
            msgs = b.get("messages") or []
            native = str(b.get("model", "")).startswith("claude") and (
                not msgs or msgs[-1].get("role") != "assistant")
            route[f"{tag}_native"] += native
            route[f"{tag}_translated"] += not native
            sys_msgs = any(m.get("role") == "system" for m in msgs)
            route[f"{tag}_native_with_role_system_message"] += native and sys_msgs
            route[f"{tag}_last_role_system"] += bool(msgs) and msgs[-1].get("role") == "system"
        cc = json.dumps(body).count('"cache_control"')
        route["shim_bodies_with_cache_control"] += cc > 0
        route["shim_cache_control_markers"] += cc
    print(json.dumps({
        "pr280_routing": dict(route),
        "requests": len(reqs),
        "by_capture_dir": dict(collections.Counter(s for s, _ in reqs)),
        "classes": dict(classes),
        "top_level_fields": dict(fields),
        "field_reaches_openai_payload": {k: reached.get(k, 0) for k in fields},
        "field_values": {k: dict(v) for k, v in values.items()},
        "other": dict(other),
        "openai_user_len_range": [min(user_len), max(user_len)] if user_len else None,
    }, indent=1))


if __name__ == "__main__":
    main()
