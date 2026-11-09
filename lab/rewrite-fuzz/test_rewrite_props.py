"""Property / fuzz tests for copilot_shim.rewrite and the shim server (lab, not CI).

Run from the repo root:
    python3 -m pytest -q -s lab/rewrite-fuzz -p no:cacheprovider
Needs `hypothesis` (not in requirements-dev.txt). Prints a census instead of
failing on robustness crashes, so one run reports every crash class.
"""
from __future__ import annotations

import copy
import http.client
import json
import pathlib
import sys
import threading

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from copilot_shim.rewrite import Policy, rewrite  # noqa: E402
from copilot_shim.server import serve  # noqa: E402
from fake_upstream import FakeUpstream  # noqa: E402

REJECTING = "claude-opus-5.5"
N = 3000  # examples per property
MSG_PATH = "/v1/" + "messages"

# ---------- well-formed Claude-Code-like Anthropic conversations ----------
text = st.text(min_size=0, max_size=12)
text_block = st.builds(lambda t: {"type": "text", "text": t}, text)
cache_text_block = st.builds(lambda t: {"type": "text", "text": t,
                                        "cache_control": {"type": "ephemeral"}}, text)
content = st.one_of(text, st.lists(st.one_of(text_block, cache_text_block), min_size=1, max_size=3))


@st.composite
def conversation(draw):
    """A valid exchange (user/assistant alternating, tool_use answered by tool_result),
    then optionally a trailing assistant prefill, with role:system messages inserted anywhere."""
    msgs, n = [], 0
    msgs.append({"role": "user", "content": draw(content)})
    for _ in range(draw(st.integers(0, 4))):
        if draw(st.booleans()):  # tool round
            n += 1
            tid = f"toolu_{n}"
            msgs.append({"role": "assistant", "content": [
                {"type": "text", "text": draw(text)},
                {"type": "tool_use", "id": tid, "name": "Bash", "input": {}}]})
            msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": draw(text)}]})
        else:
            msgs.append({"role": "assistant", "content": draw(content)})
            msgs.append({"role": "user", "content": draw(content)})
    if draw(st.booleans()):
        msgs.append({"role": "assistant", "content": draw(content)})  # prefill
    for _ in range(draw(st.integers(0, 2))):
        i = draw(st.integers(0, len(msgs)))
        msgs.insert(i, {"role": "system", "content": draw(content)})
    body = {"model": draw(st.sampled_from([REJECTING, "claude-haiku-4.5"])),
            "max_tokens": 64, "messages": msgs}
    if draw(st.booleans()):
        body["system"] = draw(content)
    return body


def merged_roles(msgs):
    out = []
    for m in msgs:
        if out and out[-1][0] == m["role"]:
            out[-1][1].append(m)
        else:
            out.append((m["role"], [m]))
    return out


def blocks(m):
    c = m.get("content")
    return c if isinstance(c, list) else []


def api_violations(body, prefill_ok):
    """Anthropic Messages API constraints the shim's output must satisfy."""
    v, msgs = [], body["messages"]
    if not msgs:
        return ["empty messages"]
    if any(m["role"] not in ("user", "assistant") for m in msgs):
        v.append("non user/assistant role")
    if msgs[-1]["role"] != "user" and not prefill_ok:
        v.append("ends on assistant")
    turns = merged_roles(msgs)
    for i, (role, group) in enumerate(turns):
        if role != "assistant":
            continue
        uses = {b["id"] for m in group for b in blocks(m) if b.get("type") == "tool_use"}
        nxt = turns[i + 1] if i + 1 < len(turns) else None
        results = {b["tool_use_id"] for m in (nxt[1] if nxt else []) for b in blocks(m)
                   if b.get("type") == "tool_result"}
        if uses - results and nxt is not None:
            v.append(f"tool_use without result: {sorted(uses - results)}")
    return v


def sys_text(body):
    s = body.get("system")
    if isinstance(s, str):
        return s
    return "".join(b.get("text", "") for b in s or [])


def all_text(x):
    if isinstance(x, str):
        return x
    return "".join(b.get("text", "") for b in x)


EVENTS = {"total": 0, "rewritten": 0, "multi_assistant_dropped": 0}


@pytest.mark.parametrize("mode", ["strip", "hint"])
@settings(max_examples=N, deadline=None, suppress_health_check=list(HealthCheck))
@given(body=conversation())
def test_wellformed_output_is_valid(mode, body):
    before = copy.deepcopy(body)
    pol = Policy(mode=mode)
    res = rewrite(body, pol, anthropic=True)
    out = res.body
    assert body == before, "input mutated"
    ok = pol.accepts_prefill(body["model"])
    assert api_violations(out, ok) == []
    # no system text is lost when folding
    want = sys_text(before) + "".join(all_text(m["content"]) for m in before["messages"]
                                      if m["role"] == "system")
    assert sys_text(out) == want
    # idempotent
    assert rewrite(out, pol, anthropic=True).body == out
    # strip removes exactly the trailing assistant run (after folding), nothing else
    nonsys = [m for m in before["messages"] if m["role"] != "system"]
    k = len(nonsys)
    while k and nonsys[k - 1]["role"] == "assistant":
        k -= 1
    if mode == "strip" and not ok:
        assert out["messages"] == nonsys[:k]
    EVENTS["total"] += 1
    EVENTS["rewritten"] += res.action != "none"
    if len(nonsys) - k > 1 and not ok:
        EVENTS["multi_assistant_dropped"] += 1


def test_report_events():
    print("\nEVENTS", EVENTS)


# ---------- arbitrary JSON bodies: rewrite must never raise ----------
json_leaf = st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=4))
json_val = st.recursive(json_leaf, lambda c: st.one_of(
    st.lists(c, max_size=3), st.dictionaries(st.sampled_from(
        ["role", "content", "type", "text", "model", "messages", "system", "x"]), c, max_size=4)),
    max_leaves=12)
role = st.sampled_from(["user", "assistant", "system", "tool", None, 1])
block = st.one_of(json_val, st.fixed_dictionaries({"type": st.just("text"), "text": json_val}))
message = st.one_of(json_val, st.fixed_dictionaries(
    {"role": role, "content": st.one_of(json_val, st.lists(block, max_size=3))}))
fuzz_body = st.fixed_dictionaries(
    {"model": st.one_of(st.just(REJECTING), json_val), "messages": st.lists(message, max_size=4)},
    optional={"system": st.one_of(json_val, st.lists(block, max_size=2))})

CRASHES: dict[str, list] = {}
FUZZ = {"total": 0}


@pytest.mark.parametrize("mode", ["strip", "hint"])
@pytest.mark.parametrize("anthropic", [True, False])
@settings(max_examples=N, deadline=None, suppress_health_check=list(HealthCheck), database=None)
@given(body=fuzz_body)
def test_never_raises(mode, anthropic, body):
    FUZZ["total"] += 1
    try:
        rewrite(body, Policy(mode=mode), anthropic=anthropic)
    except Exception as exc:  # record, don't fail: we want the census
        key = f"[{mode},{'anth' if anthropic else 'oai'}] {type(exc).__name__}: {str(exc)[:70]}"
        CRASHES.setdefault(key, []).append(body)


def test_report_crashes():
    n = sum(len(v) for v in CRASHES.values())
    print(f"\nFUZZ total={FUZZ['total']} crashing={n}")
    for k, v in sorted(CRASHES.items(), key=lambda kv: -len(kv[1])):
        smallest = min(v, key=lambda b: len(json.dumps(b)))
        print(f"CRASH x{len(v)} {k}\n   e.g. {json.dumps(smallest)}")


# ---------- end to end: what does a client see when rewrite raises? ----------
@pytest.mark.parametrize("bad", [
    {"model": REJECTING, "messages": ["hi", {"role": "assistant", "content": "x"}]},
    {"model": 5, "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "x"}]},
])
def test_server_on_rewrite_exception(bad):
    with FakeUpstream() as up:
        srv = serve("127.0.0.1", 0, up.url, Policy(mode="hint"))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            c.request("POST", MSG_PATH, body=json.dumps(bad).encode(),
                      headers={"Content-Type": "application/json"})
            try:
                r = c.getresponse()
                outcome = f"HTTP {r.status}"
            except Exception as exc:
                outcome = f"{type(exc).__name__}: {exc}"
            print(f"\nSERVER_OUTCOME {outcome} upstream_requests={len(up.received)}")
        finally:
            srv.shutdown()
            srv.server_close()
