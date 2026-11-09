"""Chain test: Claude-Code-like body -> copilot_shim.rewrite -> copilot-api translateToOpenAI.

Asks whether the shim's guarantee (the Anthropic body ends on a user turn) still holds
in the OpenAI payload copilot-api actually POSTs to Copilot, and what else the
translation does to shim output.

Run from the repo root (needs bun and a copilot-api checkout; no network, no login):
    git clone --depth 1 https://github.com/ericc-ch/copilot-api .agent-work/copilot-api
    python3 lab/copilot-translate/run.py .agent-work/copilot-api [N]
"""
from __future__ import annotations

import collections
import copy
import json
import pathlib
import random
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from copilot_shim.rewrite import Policy, rewrite  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
TRANSLATOR_REL = "src/routes/messages/non-stream-translation.ts"
REJECTING = ("claude-opus-5.5", "claude-sonnet-5.5", "claude-opus-4-8-20260101", "claude-sonnet-4-6-20260101")
ACCEPTING = ("claude-haiku-4.5",)

# ---------------------------------------------------------------- corpus

def txt(r):
    return r.choice(["ok", "Run the tests", "{", "Here is the plan:", "line\nline", "  spaced  "])


def user_content(r, kinds):
    """User content. kinds: which unusual block kinds may appear."""
    c = r.random()
    if c < 0.3:
        return txt(r)
    blocks = []
    for _ in range(r.randint(1, 3)):
        k = r.choice(["text"] * 4 + list(kinds))
        if k == "text":
            blocks.append({"type": "text", "text": txt(r)})
        elif k == "image":
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBO"}})
        elif k == "document":
            blocks.append({"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "DOC"}})
    return blocks


def assistant_content(r, thinking):
    c = r.random()
    if c < 0.3:
        return txt(r)
    blocks = []
    if thinking and r.random() < 0.5:
        blocks.append({"type": "thinking", "thinking": "THINK", "signature": "sig"})
    blocks.append({"type": "text", "text": txt(r)})
    return blocks


def conversation(r, kinds=("image", "document"), thinking=True):
    msgs, n = [{"role": "user", "content": user_content(r, kinds)}], 0
    for _ in range(r.randint(0, 5)):
        if r.random() < 0.5:  # tool round(s), possibly parallel tool calls
            ids = []
            blocks = []
            if thinking and r.random() < 0.4:
                blocks.append({"type": "thinking", "thinking": "THINK", "signature": "sig"})
            if r.random() < 0.7:
                blocks.append({"type": "text", "text": txt(r)})
            for _ in range(r.randint(1, 3)):
                n += 1
                ids.append(f"toolu_{n}")
                blocks.append({"type": "tool_use", "id": ids[-1], "name": "Bash", "input": {"cmd": "ls"}})
            msgs.append({"role": "assistant", "content": blocks})
            res = []
            for tid in ids:
                rc = r.choice(["out", [{"type": "text", "text": "out"}],
                               [{"type": "text", "text": "shot"},
                                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBO"}}]])
                res.append({"type": "tool_result", "tool_use_id": tid, "content": rc,
                            **({"is_error": True} if r.random() < 0.1 else {})})
            if r.random() < 0.3:  # Claude Code appends a text block (system-reminder) after results
                res.append({"type": "text", "text": "<system-reminder>x</system-reminder>"})
            msgs.append({"role": "user", "content": res})
        else:
            msgs.append({"role": "assistant", "content": assistant_content(r, thinking)})
            msgs.append({"role": "user", "content": user_content(r, kinds)})
    if r.random() < 0.5:
        msgs.append({"role": "assistant", "content": assistant_content(r, thinking=False)})  # prefill
    for _ in range(r.choice([0, 0, 1, 2])):
        msgs.insert(r.randint(0, len(msgs)), {"role": "system", "content": "<env>cwd=/x</env>"})
    body = {"model": r.choice(REJECTING + ACCEPTING), "max_tokens": 64, "messages": msgs}
    if r.random() < 0.8:
        body["system"] = [{"type": "text", "text": "You are Claude Code.", "cache_control": {"type": "ephemeral"}}]
    return body


EDGE = {
    # user turn whose content is an empty list
    "empty_user_list_then_prefill": {"model": "claude-opus-5.5", "max_tokens": 8, "messages": [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"},
        {"role": "user", "content": []}, {"role": "assistant", "content": "{"}]},
    # user turn with only a document block (no text)
    "document_only_user": {"model": "claude-opus-5.5", "max_tokens": 8, "messages": [
        {"role": "user", "content": [{"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "DOC"}}]},
        {"role": "assistant", "content": "Summary:"}]},
    # Claude Code 2.1.x first turn: env block as trailing role:system
    "cc21_hello_trailing_system": {"model": "claude-opus-5.5", "max_tokens": 8, "messages": [
        {"role": "user", "content": "hello"}, {"role": "system", "content": "<env>cwd=/x</env>"}]},
    # tool results only, then prefill
    "tool_results_then_prefill": {"model": "claude-opus-5.5", "max_tokens": 8, "messages": [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        {"role": "assistant", "content": "Done:"}]},
    # dated Claude Code IDs that copilot-api renames
    "dated_opus_4x": {"model": "claude-opus-4-8-20260101", "max_tokens": 8, "messages": [
        {"role": "user", "content": "hi"}]},
    "dated_opus_5x": {"model": "claude-opus-5-5-20260601", "max_tokens": 8, "messages": [
        {"role": "user", "content": "hi"}]},
    "dated_haiku_45": {"model": "claude-haiku-4-5-20251001", "max_tokens": 8, "messages": [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "{"}]},
}

# ---------------------------------------------------------------- checks

def anthropic_text(body):
    """Every text the model should see (system + message text/thinking/tool_result text)."""
    out = []
    s = body.get("system")
    out += [s] if isinstance(s, str) else [b.get("text", "") for b in s or []]
    def walk(c):
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            for b in c:
                if b.get("type") == "text":
                    out.append(b["text"])
                elif b.get("type") == "thinking":
                    out.append(b["thinking"])
                elif b.get("type") == "tool_result":
                    walk(b.get("content"))
                elif b.get("type") == "document":
                    out.append(b["source"]["data"])
    for m in body["messages"]:
        walk(m.get("content"))
    return [t for t in out if t]


def openai_blob(payload):
    return json.dumps(payload, ensure_ascii=False)


def oa_checks(oa, anth, accepting):
    v = []
    msgs = oa["messages"]
    nonsys = [m for m in msgs if m["role"] != "system"]
    if not nonsys:
        v.append("no non-system messages")
    elif nonsys[-1]["role"] == "assistant" and not accepting:
        v.append("ends on assistant (prefill 400)")
    if sum(m["role"] == "system" for m in msgs) > 1 or any(
            m["role"] == "system" for m in msgs[1:]):
        v.append("system message not first/only")
    # every tool_call answered by the immediately following tool messages
    for i, m in enumerate(msgs):
        if m["role"] == "assistant" and m.get("tool_calls"):
            want = {tc["id"] for tc in m["tool_calls"]}
            j, got = i + 1, set()
            while j < len(msgs) and msgs[j]["role"] == "tool":
                got.add(msgs[j]["tool_call_id"]); j += 1
            if want - got and j < len(msgs):
                v.append("tool_call not answered")
    for m in msgs:
        c = m.get("content")
        if m["role"] in ("user", "tool") and (c == "" or c == []):
            v.append(f"empty {m['role']} content")
        if m["role"] == "tool" and isinstance(c, list) and any(p.get("type") == "image_url" for p in c):
            v.append("image inside tool message")
    blob = openai_blob(oa)
    lost = [t for t in anthropic_text(anth) if json.dumps(t, ensure_ascii=False)[1:-1] not in blob]
    if lost:
        v.append("text lost: " + ",".join(sorted(set(lost))))
    if any(m["role"] == "assistant" and "THINK" in json.dumps(m.get("content")) for m in msgs):
        v.append("thinking sent as assistant text")
    return v


def initiator(oa):
    return "agent" if any(m["role"] in ("assistant", "tool") for m in oa["messages"]) else "user"


# ---------------------------------------------------------------- main

def translate(repo, bodies):
    inp = "\n".join(json.dumps(b) for b in bodies) + "\n"
    p = subprocess.run(["bun", str(HERE / "translate.ts"), str((pathlib.Path(repo) / TRANSLATOR_REL).resolve())],
                       input=inp, capture_output=True, text=True, check=True)
    return [json.loads(line) for line in p.stdout.splitlines()]


def main():
    repo = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    r = random.Random(20261109)
    fixtures = json.loads((ROOT / "tests/fixtures/prefill_requests.json").read_text())
    cases = [(f"fixture:{f['name']}", f["body"]) for f in fixtures if "messages" in f["path"]]
    cases += [(f"edge:{k}", b) for k, b in EDGE.items()]
    cases += [(f"gen:{i}", conversation(r)) for i in range(n)]

    jobs = []  # (case, mode, anthropic body sent by shim)
    for name, body in cases:
        for mode in ("off", "strip", "hint"):
            res = rewrite(copy.deepcopy(body), Policy(mode=mode), anthropic=True)
            jobs.append((name, mode, body, res.body))
    outs = translate(repo, [j[3] for j in jobs])

    stats = collections.defaultdict(collections.Counter)
    examples = {}
    detail = []
    for (name, mode, orig, shimmed), out in zip(jobs, outs):
        key = mode
        stats[key]["bodies"] += 1
        if "error" in out:
            stats[key]["translator exception"] += 1
            examples.setdefault((mode, "translator exception"), (name, out["error"]))
            continue
        oa = out["ok"]
        accepting = Policy().accepts_prefill(shimmed.get("model"))
        stats[key][f"X-Initiator={initiator(oa)}"] += 1
        if oa["model"] != shimmed["model"]:
            stats[key][f"model renamed {shimmed['model']} -> {oa['model']}"] += 1
        for v in sorted(set(oa_checks(oa, shimmed, accepting))):  # count each class once per body
            cls = v.split(":")[0]
            stats[key][cls] += 1
            if cls == "text lost":
                for t in v.split(": ", 1)[1].split(","):
                    stats[key][f"  text lost includes {t!r}"] += 1
            examples.setdefault((mode, cls), (name, v))
        if not name.startswith("gen:"):
            detail.append({"case": name, "mode": mode, "last_role": (oa["messages"] or [{}])[-1].get("role"),
                           "roles": [m["role"] for m in oa["messages"]], "model": oa["model"],
                           "initiator": initiator(oa), "violations": oa_checks(oa, shimmed, accepting)})

    report = {"n_generated": n, "stats": {k: dict(sorted(c.items())) for k, c in stats.items()},
              "examples": {f"{m} / {c}": e for (m, c), e in examples.items()}, "named_cases": detail}
    (HERE / "results.json").write_text(json.dumps(report, indent=1))
    for mode, c in stats.items():
        print(f"== mode={mode}")
        for k, val in sorted(c.items()):
            print(f"  {val:6}  {k}")
    print("\n== named cases (mode: last OpenAI role, initiator, violations)")
    for d in detail:
        print(f"  {d['case']:42} {d['mode']:5} last={d['last_role']!s:9} {d['initiator']:5} "
              f"model={d['model']:22} {d['violations']}")


if __name__ == "__main__":
    main()
