"""How much of each request's prompt can Copilot serve from its prefix cache, per shim mode?

Pipeline per request: Claude Code body -> shim transform -> copilot-api translateToOpenAI
(the real one, via ../copilot-translate/translate.ts and bun) -> rendered prompt
(tools, then messages in order). Cacheable tokens of request i = longest common prefix
with any earlier request of the same session and mode (an ideal prefix cache with no
expiry), in tokens ~= chars/4. A cache that only works in 128-token steps above 1024
tokens is also reported (OpenAI's documented rule; Copilot's rule for Claude is unknown).

Transforms:
  off      no change (the request with a trailing role:"system" message then fails
           with the prefill 400; listed only as the append-only baseline)
  strip    copilot_shim.rewrite as on main: every role:"system" message is folded into
           the top-level system field, then the prefill is stripped
  inplace  prototype: every role:"system" message becomes a role:"user" message where
           it stands (moved after the tool_result if it splits a tool_use from it),
           then the prefill is stripped. History stays append-only.

Usage:
  python3 lab/cache-prefix/analyze.py <copilot-api checkout> capture <capture.jsonl>     (writes <capture>.result.json)
  python3 lab/cache-prefix/analyze.py <copilot-api checkout> sim <capture.jsonl> [seed]
"""
from __future__ import annotations

import copy
import json
import pathlib
import random
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from copilot_shim.rewrite import Policy, rewrite  # noqa: E402

TRANSLATE_TS = ROOT / "lab/copilot-translate/translate.ts"
MODES = ("off", "strip", "inplace")
# Copilot AI-credit rates, USD per 1M tokens (docs.github.com models-and-pricing, 2026-11-09)
RATES = {"claude-opus-5.5": dict(inp=4.00, cached=0.20, write=5.00),
         "claude-sonnet-5.5": dict(inp=2.00, cached=0.10, write=2.50)}


def system_to_user_inplace(body: dict) -> dict:
    out = copy.deepcopy(body)
    msgs = out.get("messages") or []
    new = []
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "system":
            c = m.get("content")
            blocks = [{"type": "text", "text": c}] if isinstance(c, str) else [
                {"type": "text", "text": b["text"]} for b in c or () if isinstance(b, dict) and b.get("type") == "text"]
            new.append({"role": "user", "content": blocks})
        else:
            new.append(m)
    # never separate an assistant tool_use from its tool_result
    def has(m, t):
        return isinstance(m.get("content"), list) and any(b.get("type") == t for b in m["content"])
    i = 1
    while i < len(new) - 1:
        if (new[i - 1].get("role") == "assistant" and has(new[i - 1], "tool_use")
                and new[i].get("role") == "user" and not has(new[i], "tool_result") and has(new[i + 1], "tool_result")):
            new[i], new[i + 1] = new[i + 1], new[i]
        i += 1
    out["messages"] = new
    return out


def transform(body: dict, mode: str) -> dict:
    if mode == "off":
        return body
    if mode == "strip":
        return rewrite(body, Policy(mode="strip"), anthropic=True).body
    return rewrite(system_to_user_inplace(body), Policy(mode="strip"), anthropic=True).body


def translate(bodies: list[dict], copilot_api: pathlib.Path) -> list[dict]:
    src = copilot_api / "src/routes/messages/non-stream-translation.ts"
    p = subprocess.run(["bun", str(TRANSLATE_TS), str(src)], input="\n".join(json.dumps(b) for b in bodies) + "\n",
                       capture_output=True, text=True, check=True)
    outs = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
    assert len(outs) == len(bodies), p.stderr[-2000:]
    return [o["ok"] for o in outs]


def render(oa: dict) -> str:
    return json.dumps(oa.get("tools"), sort_keys=True) + "".join(
        json.dumps(m, sort_keys=True) for m in oa["messages"])


def lcp(a: str, b: str) -> int:
    n = min(len(a), len(b))
    lo, hi = 0, n
    while lo < hi:  # binary search on prefix equality
        mid = (lo + hi + 1) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def session_stats(prompts: list[str], model: str) -> dict:
    rows, seen = [], []
    for p in prompts:
        hit = max((lcp(p, q) for q in seen), default=0)
        tot, c = len(p) // 4, hit // 4
        c_oai = (c // 128) * 128 if c >= 1024 else 0
        rows.append((tot, c, c_oai))
        seen.append(p)
    r = RATES[model]
    tot = sum(t for t, _, _ in rows)
    cached = sum(c for _, c, _ in rows)
    cached_oai = sum(c for _, _, c in rows)
    cost = lambda cch, miss_rate: (cch * r["cached"] + (tot - cch) * miss_rate) / 1e6  # noqa: E731
    return dict(requests=len(rows), prompt_tokens=tot, cached_tokens=cached, cached_tokens_oai_rule=cached_oai,
                hit_rate=round(cached / tot, 4), usd_input=round(cost(cached, r["inp"]), 4),
                usd_input_if_miss_is_cache_write=round(cost(cached, r["write"]), 4),
                per_request=[dict(tokens=t, cached=c) for t, c, _ in rows])


def check_payload(oa: dict) -> list[str]:
    """Same invariants the copilot-translate lab checked."""
    probs = []
    roles = [m["role"] for m in oa["messages"] if m["role"] != "system"]
    if roles and roles[-1] == "assistant":
        probs.append("ends-on-assistant")
    msgs = oa["messages"]
    for i, m in enumerate(msgs):
        answered, j = set(), i + 1
        while j < len(msgs) and msgs[j]["role"] == "tool":
            answered.add(msgs[j].get("tool_call_id"))
            j += 1
        if any(tc["id"] not in answered for tc in m.get("tool_calls") or []):
            probs.append("tool-call-unanswered")
    return probs


def run_session(bodies: list[dict], copilot_api: pathlib.Path) -> dict:
    model = bodies[0]["model"]
    res = {}
    for mode in MODES:
        oas = translate([transform(b, mode) for b in bodies], copilot_api)
        st = session_stats([render(o) for o in oas], model)
        st["payload_problems"] = sum(bool(check_payload(o)) for o in oas)
        res[mode] = st
    return res


# ------------------------------------------------------------------ synthetic sessions

def synth_session(template: dict, rng: random.Random, n_requests: int, n_updates: int) -> list[dict]:
    """Grow a Claude-Code-shaped session from a captured first request.

    Each step is either a tool round (assistant tool_use + user tool_result of 200-6000
    tokens) or a new user prompt after an assistant reply. `n_updates` requests at random
    points append a sticky role:"system" '# Environment update' message after the last
    user turn, the way Claude Code 2.1.321 does after a `cd` (see capture).
    """
    base = copy.deepcopy(template)
    msgs = base["messages"][:2]  # first user turn + the 9 kB env system message
    upd_at = set(rng.sample(range(5, n_requests), n_updates)) if n_updates else set()
    bodies, k = [], 0
    for i in range(n_requests):
        if i in upd_at:
            msgs.append({"role": "system", "content": [{"type": "text", "text":
                         f"# Environment update\n - Primary working directory: /w/p{i} (was /w/p{i - 1})"}]})
        bodies.append(dict(copy.deepcopy(base), messages=copy.deepcopy(msgs)))
        k += 1
        if rng.random() < 0.8:
            tid = f"toolu_{k}"
            msgs.append({"role": "assistant", "content": [
                {"type": "text", "text": "Working." * rng.randint(1, 40)},
                {"type": "tool_use", "id": tid, "name": "Read", "input": {"file_path": f"/w/f{k}"}}]})
            msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                      "content": "x" * (4 * rng.randint(200, 6000))}]})
        else:
            msgs.append({"role": "assistant", "content": [{"type": "text", "text": "Done. " * rng.randint(5, 200)}]})
            msgs.append({"role": "user", "content": [{"type": "text", "text": "next " * rng.randint(3, 60)}]})
    return bodies


def main() -> None:
    copilot_api = pathlib.Path(sys.argv[1]).resolve()
    what, cap = sys.argv[2], sys.argv[3]
    bodies = [json.loads(line)["body"] for line in open(cap)]
    bodies = [b for b in bodies if isinstance(b, dict) and b.get("messages") and b.get("tools")]
    if what == "capture":
        res = run_session(bodies, copilot_api)
        for mode, st in res.items():
            print(f"{mode:8} problems={st['payload_problems']} hit={st['hit_rate']:.3f} tokens={st['prompt_tokens']} "
                  f"per-request cached/total: " + " ".join(f"{r['cached']}/{r['tokens']}" for r in st["per_request"]))
        json.dump(res, open(pathlib.Path(cap).with_suffix(".result.json"), "w"), indent=1)
        return
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    rng = random.Random(seed)
    out = {}
    for n_updates in (0, 1, 3, 10):
        agg = {m: dict(prompt_tokens=0, cached_tokens=0, usd_input=0.0, problems=0) for m in MODES}
        reps = 5
        for _ in range(reps):
            res = run_session(synth_session(bodies[0], rng, 120, n_updates), copilot_api)
            for m in MODES:
                agg[m]["prompt_tokens"] += res[m]["prompt_tokens"] / reps
                agg[m]["cached_tokens"] += res[m]["cached_tokens"] / reps
                agg[m]["usd_input"] += res[m]["usd_input"] / reps
                agg[m]["problems"] += res[m]["payload_problems"]
        for m in MODES:
            a = agg[m]
            a["hit_rate"] = round(a["cached_tokens"] / a["prompt_tokens"], 4)
            a["usd_input"] = round(a["usd_input"], 3)
            a["prompt_tokens"] = round(a["prompt_tokens"])
            a["cached_tokens"] = round(a["cached_tokens"])
        out[n_updates] = agg
        print(f"updates={n_updates:2}  " + "  ".join(
            f"{m}: hit={agg[m]['hit_rate']:.3f} ${agg[m]['usd_input']:.2f} probs={agg[m]['problems']}" for m in MODES))
    json.dump(out, open(pathlib.Path(__file__).with_name("sim_results.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
