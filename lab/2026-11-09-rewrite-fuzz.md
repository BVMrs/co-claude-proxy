# 2026-11-09 — Property and fuzz tests of `copilot_shim.rewrite`

## Question

The milestone is to get Claude Code working through the shim against real Copilot. Its
last open step, "record which requests the shim strips", needs a Copilot login. Without one,
two questions can still be tested:

1. For any well-formed Claude-Code-like request, does `rewrite()` produce a body that
   the Anthropic Messages API accepts? That means only user/assistant roles, no trailing
   assistant turn, every `tool_use` answered, no system text lost, input not mutated,
   and idempotent.
2. For malformed JSON, does `rewrite()` (and so the server) ever raise? If it does, what
   does the client see?

## Method

`lab/rewrite-fuzz/test_rewrite_props.py` (Hypothesis 6.136.9, Python 3.13.12, against
`main` @ 960daf7; this branch has no code changes):

- **Well-formed generator:** a user turn; 0–4 rounds, each either text or
  `tool_use`→`tool_result`; an optional trailing assistant prefill; 0–2 `role:"system"`
  messages inserted at random positions; an optional top-level `system` (string or
  blocks, some with `cache_control`); model `claude-opus-5.5` or `claude-haiku-4.5`.
  It checks 6 properties against the output. 3000 examples × modes `strip`/`hint`.
- **Fuzz generator:** arbitrary JSON for `model`, `messages[]`, `content`, block `text`,
  and `system`. 3000 examples × {strip, hint} × {anthropic, openai} = 12 000 bodies. A
  raised exception is recorded, not failed, so one run lists every crash class.
- **End to end:** two crashing bodies sent through `serve()` to `tests/fake_upstream.py`.
- `lab/rewrite-fuzz/repro.py` has a one-line repro for each class.

Run: `python3 -m pytest -q -s lab/rewrite-fuzz -p no:cacheprovider` (~2.5 min).

## Results

**Well-formed inputs: 6000/6000 pass all properties.** `rewrite` changed 1558 of them
(action `stripped` or `hinted`). The output is always valid for the API:

- no `system` role is left;
- the body ends on a user turn for models that reject prefill;
- `tool_use`/`tool_result` pairs survive. This includes a `system` message placed
  between a `tool_use` and its `tool_result`: folding makes the two adjacent again;
- the system text is kept and stays in order;
- the input is never mutated;
- `rewrite(rewrite(x)) == rewrite(x)`;
- in `strip` mode, exactly the trailing assistant run is removed.

The existing suite still passes: 83 tests.

**Malformed inputs: 491 of 12 000 fuzz bodies (4.1%) raise.** They reduce to 4 root
causes (repro output):

| # | Input | Exception | Where |
|---|---|---|---|
| R1 | `model` not a string (e.g. `5`) and the body ends on assistant | `TypeError` from `fnmatchcase` | `Policy.accepts_prefill` |
| R2 | `hint` mode, the message before the prefill isn't a dict | `AttributeError: 'str' has no .get` | `rewrite`, `target.get("role")` |
| R3 | `content` is int/bool, in a folded `system` message or in the prefill (`hint`) | `TypeError: not iterable` | `_system_blocks`, `_text_of` |
| R4 | text block whose `text` isn't a string (`null`, dict, list), `hint` | `TypeError` in `"".join` | `_text_of` |

R1 dominates: it covers most of the 491. **End to end:** for both bodies tested (R1 and
R2), the server raised inside the handler and closed the socket. The client got
`RemoteDisconnected: Remote end closed connection without response`. The upstream got
**0 requests**, so the client never sees the 400 that would name the field Copilot
rejected. It sees a network error instead, and Claude Code treats network errors as
retryable.

**Impact:** low. None of R1–R4 is a valid Anthropic or OpenAI request, so Claude Code
should not send them. Still, the shim turns "upstream validation error" into "connection
reset with no log line except a traceback on stderr", which is the wrong failure mode
for a debugging tool.

**Semantic note (S1, not a crash):** when a conversation ends in two consecutive
assistant messages, `strip` removes both. That includes the case where the first is a real
answer and only the second is a prefill: `[user, assistant "The answer is 42.",
assistant "{"]` becomes `[user]`. This is documented behaviour (fixture
`two_trailing_assistant`). The well-formed generator never produced this shape
(counter = 0), because it only emits alternating turns. It only matters if Claude Code
really sends such a body. The pending real-Copilot capture would show whether it does.

**Not covered:** SSE relay, `count_tokens` semantics (`rewrite` also strips prefill there,
so token counts differ slightly from the real request), and OpenAI-shape tool messages.
The generator does not cover thinking blocks or images.

## What the planner should do next

1. Small PR (about 20 lines plus tests): make `rewrite()` total. Treat a non-string model
   as "does not accept prefill". Skip non-dict messages and blocks, and non-string
   `text`, in `_text_of`/`_system_blocks`/the hint target. Also wrap the `rewrite`
   call in `server._forward` with `try/except Exception` that logs and forwards the
   **original** body unchanged, so the upstream's 400 reaches the client.
   Turn `repro.py`'s 5 crash cases into regression tests in `tests/test_rewrite.py`.
2. Optional: add `hypothesis` to `requirements-dev.txt` and promote the well-formed
   property test, with ~300 examples to keep CI fast, as a guard for future rewrite modes.
3. Leave S1 alone unless the real-Copilot capture (HANDOVER "still to do" #2) shows a
   double-assistant tail. If it does, strip only the last assistant message.

## Sources

- Code under test: `copilot_shim/rewrite.py`, `copilot_shim/server.py` @ 960daf7.
- Anthropic Messages API constraints used by the validator: roles user/assistant only,
  consecutive same-role turns merged, each `tool_use` answered by a `tool_result` in the
  next user turn — https://docs.anthropic.com/en/api/messages and
  https://docs.anthropic.com/en/docs/build-with-claude/tool-use
- Hypothesis: https://hypothesis.readthedocs.io/
