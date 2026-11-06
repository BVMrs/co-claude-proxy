"""Claude Code 2.1.x sends its environment block as a trailing role:"system" message.

Shape captured from `claude -p hello` (Claude Code 2.1.318) on 2026-11-06, with the
text shortened. copilot-api turns that message into an assistant turn, so Copilot
answers a plain "hello" with the prefill 400.
"""

import copy

from test_replay import post

from copilot_shim.rewrite import Policy, rewrite
from fake_upstream import PREFILL_ERROR

SYSTEM_BLOCK = {"type": "text", "text": "You are Claude Code."}
ENV_BLOCK = {"type": "text", "text": "# Environment\n - Platform: linux",
             "cache_control": {"type": "ephemeral", "ttl": "1h"}}

HELLO = {
    "model": "claude-opus-5.5",
    "max_tokens": 32000,
    "stream": True,
    "system": [SYSTEM_BLOCK],
    "thinking": {"type": "adaptive", "display": "omitted"},
    "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "<system-reminder>\ncontext\n</system-reminder>"},
            {"type": "text", "text": "hello"},
        ]},
        {"role": "system", "content": [ENV_BLOCK]},
    ],
}


def test_trailing_system_message_folded_into_system_field():
    before = copy.deepcopy(HELLO)
    res = rewrite(HELLO, Policy(), anthropic=True)
    assert HELLO == before, "input must not be mutated"
    assert res.last_role == "system"
    assert res.system_folded == 1
    assert res.action == "none"
    assert res.body["messages"] == HELLO["messages"][:1]
    assert res.body["system"] == [SYSTEM_BLOCK, ENV_BLOCK]


def test_folding_happens_for_models_that_accept_prefill():
    body = dict(copy.deepcopy(HELLO), model="claude-haiku-4.5")
    res = rewrite(body, Policy(), anthropic=True)
    assert res.system_folded == 1
    assert [m["role"] for m in res.body["messages"]] == ["user"]


def test_string_system_and_string_content():
    body = {"model": "claude-opus-5.5", "system": "base",
            "messages": [{"role": "user", "content": "hi"}, {"role": "system", "content": "env"}]}
    res = rewrite(body, Policy(), anthropic=True)
    assert res.body["system"] == [{"type": "text", "text": "base"}, {"type": "text", "text": "env"}]
    assert res.body["messages"] == [{"role": "user", "content": "hi"}]


def test_system_then_assistant_prefill_is_folded_and_stripped():
    body = {"model": "claude-opus-5.5", "messages": [
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "env"},
        {"role": "assistant", "content": "{"}]}
    res = rewrite(body, Policy(), anthropic=True)
    assert res.system_folded == 1 and res.action == "stripped"
    assert res.body["messages"] == [{"role": "user", "content": "hi"}]
    assert res.body["system"] == [{"type": "text", "text": "env"}]


def test_openai_shape_keeps_system_messages():
    body = {"model": "claude-opus-5.5", "messages": [
        {"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]}
    res = rewrite(body, Policy())
    assert res.system_folded == 0 and res.body == body


def test_mode_off_leaves_system_message():
    res = rewrite(HELLO, Policy(mode="off"), anthropic=True)
    assert res.system_folded == 0 and res.body == HELLO


def test_hello_fails_without_shim(upstream):
    status, _, data = post(upstream.url, "/v1/messages", HELLO)
    assert status == 400 and PREFILL_ERROR in data.decode()


def test_hello_succeeds_through_shim(upstream, make_shim):
    status, _, data = post(make_shim(), "/v1/messages", HELLO)
    assert status == 200, data
    sent = upstream.received[-1][2]
    assert [m["role"] for m in sent["messages"]] == ["user"]
    assert sent["system"][-1] == ENV_BLOCK


def test_count_tokens_also_folded(upstream, make_shim):
    post(make_shim(), "/v1/messages/count_tokens", HELLO)
    sent = upstream.received[-1][2]
    assert [m["role"] for m in sent["messages"]] == ["user"]
