"""OpenAI chat.completion.chunk streams of the kind Copilot could send for a Claude turn.

Each scenario is a list of chunks (dicts) ending in "[DONE]". The intended assistant
turn is recorded next to it: text and tool calls (id, name, arguments JSON), so a
translation can be checked against what the model actually produced.

Tool calls use Claude Code's Bash tool with harmless commands (echo), so the same
scenarios drive the end-to-end run with the real Claude Code binary.
"""
import json

MODEL = "claude-opus-5.5"
USAGE = {"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240,
         "prompt_tokens_details": {"cached_tokens": 0}}


def ch(delta=None, finish=None, index=0, usage=None, choices=None):
    c = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1, "model": MODEL,
         "choices": choices if choices is not None else
         [{"index": index, "delta": delta or {}, "finish_reason": finish, "logprobs": None}]}
    if usage:
        c["usage"] = usage
    return c


def tc_start(i, tid, name, args=""):
    return {"index": i, "id": tid, "type": "function", "function": {"name": name, "arguments": args}}


def tc_args(i, args):
    return {"index": i, "function": {"arguments": args}}


def bash(cmd):
    return json.dumps({"command": cmd, "description": "print a marker"})


A, B = bash("echo MARK_A"), bash("echo MARK_B")
TEXT = "I'll run both."


def split(s, n=3):
    k = max(1, len(s) // n)
    return [s[i:i + k] for i in range(0, len(s), k)]


def text_chunks(t=TEXT):
    return [ch({"role": "assistant", "content": ""})] + [ch({"content": p}) for p in split(t, 2)]


def tool_seq(i, tid, args):
    parts = split(args)
    return [ch({"tool_calls": [tc_start(i, tid, "Bash")]})] + \
           [ch({"tool_calls": [tc_args(i, p)]}) for p in parts]


def fin(reason="tool_calls"):
    return ch({}, finish=reason, usage=USAGE)


TA = {"id": "toolu_A", "name": "Bash", "args": A}
TB = {"id": "toolu_B", "name": "Bash", "args": B}

S = {}  # name -> (chunks, intended, note)

S["s01_text_only"] = (text_chunks("ok.") + [fin("stop"), "[DONE]"],
                      {"text": "ok.", "tools": []}, "control: plain text")
S["s02_one_tool"] = (text_chunks() + tool_seq(0, "toolu_A", A) + [fin(), "[DONE]"],
                     {"text": TEXT, "tools": [TA]}, "control: text + one tool call, args in 3 chunks")
S["s03_parallel_sequential"] = (
    text_chunks() + tool_seq(0, "toolu_A", A) + tool_seq(1, "toolu_B", B) + [fin(), "[DONE]"],
    {"text": TEXT, "tools": [TA, TB]}, "two tool calls, one after the other (OpenAI's usual order)")
S["s04_parallel_one_chunk"] = (
    text_chunks() + [ch({"tool_calls": [tc_start(0, "toolu_A", "Bash", A), tc_start(1, "toolu_B", "Bash", B)]}),
                     fin(), "[DONE]"],
    {"text": TEXT, "tools": [TA, TB]}, "both tool calls complete in a single chunk")
pa, pb = split(A), split(B)
S["s05_parallel_interleaved"] = (
    text_chunks() + [ch({"tool_calls": [tc_start(0, "toolu_A", "Bash")]}),
                     ch({"tool_calls": [tc_start(1, "toolu_B", "Bash")]})]
    + [ch({"tool_calls": [tc_args(0, x), tc_args(1, y)]}) for x, y in zip(pa, pb)]
    + [ch({"tool_calls": [tc_args(1, y)]}) for y in pb[len(pa):]]
    + [ch({"tool_calls": [tc_args(0, x)]}) for x in pa[len(pb):]] + [fin(), "[DONE]"],
    {"text": TEXT, "tools": [TA, TB]}, "both calls start, then their arguments interleave")
S["s06_tools_in_choice_1_same_chunk"] = (
    [ch(choices=[{"index": 0, "delta": {"role": "assistant", "content": TEXT}, "finish_reason": None},
                 {"index": 1, "delta": {"tool_calls": [tc_start(0, "toolu_A", "Bash", A)]}, "finish_reason": None}]),
     ch(choices=[{"index": 0, "delta": {}, "finish_reason": "tool_calls"},
                 {"index": 1, "delta": {}, "finish_reason": "tool_calls"}], usage=USAGE), "[DONE]"],
    {"text": TEXT, "tools": [TA]}, "text in choices[0], tool call in choices[1] of the same chunk "
    "(the shape copilot-api's non-stream path merges)")
S["s07_tools_in_choice_1_own_chunks"] = (
    text_chunks() + [ch({"tool_calls": [tc_start(0, "toolu_A", "Bash", A)]}, index=1),
                     ch({}, finish="tool_calls", index=1, usage=USAGE), "[DONE]"],
    {"text": TEXT, "tools": [TA]}, "tool call in choice index 1, but alone in its chunks")
S["s08_finish_then_usage_chunk_with_choice"] = (
    text_chunks() + tool_seq(0, "toolu_A", A) + [ch({}, finish="tool_calls"),
                                                 ch({}, finish="tool_calls", usage=USAGE), "[DONE]"],
    {"text": TEXT, "tools": [TA]}, "finish_reason repeated in a trailing usage chunk")
S["s09_no_finish_reason"] = (
    text_chunks() + tool_seq(0, "toolu_A", A) + ["[DONE]"],
    {"text": TEXT, "tools": [TA]}, "stream ends ([DONE]) without any finish_reason")
S["s10_truncated_upstream"] = (
    text_chunks() + tool_seq(0, "toolu_A", A)[:2],
    {"text": TEXT, "tools": [TA], "truncated": True}, "upstream connection ends mid tool call, no [DONE]")
S["s11_length_mid_tool"] = (
    text_chunks() + tool_seq(0, "toolu_A", A)[:2] + [fin("length"), "[DONE]"],
    {"text": TEXT, "tools": [TA], "truncated": True}, "max_tokens hit inside the tool arguments")
S["s12_content_filter"] = (
    text_chunks("Here is part of the ans") + [fin("content_filter"), "[DONE]"],
    {"text": "Here is part of the ans", "tools": [], "filtered": True}, "Copilot content filter cuts the reply")
S["s13_same_index_two_ids"] = (
    text_chunks() + tool_seq(0, "toolu_A", A) + tool_seq(0, "toolu_B", B) + [fin(), "[DONE]"],
    {"text": TEXT, "tools": [TA, TB]}, "two calls both sent with tool_calls index 0 (distinct ids)")
S["s14_name_after_id"] = (
    text_chunks() + [ch({"tool_calls": [{"index": 0, "id": "toolu_A", "type": "function",
                                         "function": {"arguments": ""}}]}),
                     ch({"tool_calls": [{"index": 0, "function": {"name": "Bash", "arguments": ""}}]})]
    + [ch({"tool_calls": [tc_args(0, p)]}) for p in split(A)] + [fin(), "[DONE]"],
    {"text": TEXT, "tools": [TA]}, "id arrives one chunk before the function name")
S["s15_id_repeated_each_chunk"] = (
    text_chunks() + [ch({"tool_calls": [tc_start(0, "toolu_A", "Bash", p)]}) for p in split(A)]
    + [fin(), "[DONE]"],
    {"text": TEXT, "tools": [TA]}, "every argument chunk repeats id and name")
S["s16_text_after_tool"] = (
    text_chunks() + tool_seq(0, "toolu_A", A) + [ch({"content": " Running it now."}), fin(), "[DONE]"],
    {"text": TEXT + " Running it now.", "tools": [TA]}, "a text delta after the tool call started")
S["s17_empty_first_chunk_choices"] = (
    [ch(choices=[])] + text_chunks() + tool_seq(0, "toolu_A", A) + [fin(), "[DONE]"],
    {"text": TEXT, "tools": [TA]}, "Azure-style first chunk with choices: [] (prompt filter results)")
S["s18_usage_only_after_finish"] = (
    text_chunks() + tool_seq(0, "toolu_A", A) + [ch({}, finish="tool_calls"), ch(choices=[], usage=USAGE), "[DONE]"],
    {"text": TEXT, "tools": [TA]}, "OpenAI include_usage shape: usage in a final choices: [] chunk")


def build():
    return {k: v[0] for k, v in S.items()}, {k: v[1] for k, v in S.items()}, {k: v[2] for k, v in S.items()}


if __name__ == "__main__":
    print(json.dumps(build()[0]))
