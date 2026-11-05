import copy

import pytest
from conftest import FIXTURES

from copilot_shim.rewrite import HINT_TEMPLATE, Policy, rewrite


def ids(fx):
    return [f["name"] for f in fx]


@pytest.mark.parametrize("fx", FIXTURES, ids=ids(FIXTURES))
def test_strip_ends_in_user_and_keeps_prefix(fx):
    body = fx["body"]
    before = copy.deepcopy(body)
    res = rewrite(body, Policy())
    assert body == before, "input must not be mutated"
    assert res.action == "stripped"
    assert res.last_role == "assistant"
    msgs = res.body["messages"]
    assert msgs[-1]["role"] != "assistant"
    # Everything before the trailing assistant run is untouched.
    assert msgs == before["messages"][: len(msgs)]
    assert res.removed == len(before["messages"]) - len(msgs)
    # Non-message fields are untouched.
    assert {k: v for k, v in res.body.items() if k != "messages"} == \
           {k: v for k, v in before.items() if k != "messages"}


def test_two_trailing_assistant_both_removed():
    fx = next(f for f in FIXTURES if f["name"] == "two_trailing_assistant")
    res = rewrite(fx["body"], Policy())
    assert res.removed == 2
    assert res.body["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.parametrize("fx", FIXTURES, ids=ids(FIXTURES))
def test_hint_moves_prefill_text_into_last_user_turn(fx):
    res = rewrite(fx["body"], Policy(mode="hint"))
    assert res.action == "hinted"
    last = res.body["messages"][-1]
    assert last["role"] == "user"
    blocks = last["content"]
    assert isinstance(blocks, list)
    assert blocks[-1]["type"] == "text"
    assert blocks[-1]["text"].startswith(HINT_TEMPLATE.split("{")[0])


def test_hint_after_tool_result_keeps_tool_result_first():
    fx = next(f for f in FIXTURES if f["name"] == "after_tool_result")
    res = rewrite(fx["body"], Policy(mode="hint"))
    blocks = res.body["messages"][-1]["content"]
    assert blocks[0]["type"] == "tool_result"
    assert blocks[-1]["text"].endswith("There are two files:")


def test_hint_with_string_user_content():
    fx = next(f for f in FIXTURES if f["name"] == "probe_string_prefill")
    res = rewrite(fx["body"], Policy(mode="hint"))
    assert res.body["messages"][-1]["content"] == [
        {"type": "text", "text": "Reply with one word: ok"},
        {"type": "text", "text": HINT_TEMPLATE.format(prefill="The word is")},
    ]


def test_hint_without_text_falls_back_to_strip():
    body = {"model": "claude-opus-5.5", "messages": [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": [{"type": "text", "text": "   "}]}]}
    res = rewrite(body, Policy(mode="hint"))
    assert res.action == "stripped"
    assert res.body["messages"] == [{"role": "user", "content": "x"}]


def test_hint_after_openai_tool_turn_adds_user_turn():
    body = {"model": "claude-opus-5.5", "messages": [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "out"},
        {"role": "assistant", "content": "So"}]}
    res = rewrite(body, Policy(mode="hint"))
    assert res.body["messages"][-2]["role"] == "tool"
    assert res.body["messages"][-1]["role"] == "user"


@pytest.mark.parametrize("model", ["claude-haiku-4.5"])
def test_prefill_ok_model_passes_through(model):
    body = {"model": model, "messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]}
    res = rewrite(body, Policy())
    assert res.action == "none"
    assert res.body == body


def test_prefill_ok_globs_and_empty_list():
    body = {"model": "claude-haiku-4.5", "messages": [{"role": "user", "content": "x"},
                                                      {"role": "assistant", "content": "y"}]}
    assert rewrite(body, Policy(prefill_ok=("claude-*-4.5",))).action == "none"
    assert rewrite(body, Policy(prefill_ok=())).action == "stripped"


def test_user_last_is_untouched():
    body = {"model": "claude-opus-5.5", "messages": [{"role": "user", "content": "x"}]}
    res = rewrite(body, Policy())
    assert res.action == "none" and res.body == body and res.last_role == "user"


def test_mode_off_only_reports():
    fx = FIXTURES[0]
    res = rewrite(fx["body"], Policy(mode="off"))
    assert res.action == "none" and res.body == fx["body"] and res.last_role == "assistant"


def test_only_assistant_messages_left_alone():
    body = {"model": "claude-opus-5.5", "messages": [{"role": "assistant", "content": "y"}]}
    assert rewrite(body, Policy()).action == "none"


def test_model_map_applies_before_prefill_check():
    body = {"model": "claude-haiku-4-5-20251001", "messages": [
        {"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]}
    res = rewrite(body, Policy(model_map={"claude-haiku-4-5-20251001": "claude-haiku-4.5"}))
    assert res.body["model"] == "claude-haiku-4.5"
    assert res.original_model == "claude-haiku-4-5-20251001"
    assert res.action == "none"


@pytest.mark.parametrize("body", [None, [], {}, {"messages": []}, {"messages": "x"}])
def test_odd_bodies_do_not_crash(body):
    rewrite(body, Policy())


def test_bad_mode():
    with pytest.raises(ValueError):
        Policy(mode="nope")
