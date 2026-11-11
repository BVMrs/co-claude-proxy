# 2026-11-11: copilot-api's streaming back-translation vs Claude Code's tool loop

## Question

Every Claude Code turn through the chain comes back as an SSE stream. Copilot sends OpenAI
`chat.completion.chunk`s. copilot-api 0.7.0 turns each chunk into Anthropic events with
`translateChunkToAnthropicEvents` (`src/routes/messages/stream-translation.ts`). The shim
relays those events byte for byte (sse-relay). Earlier lab notes tested only the request
direction (copilot-translate, tool-search, thinking-fields) and usage numbers (compact-cap).

> Which OpenAI chunk shapes does copilot-api's stream translator get wrong? What does real
> Claude Code then do: run the tools, retry, or fail the turn? Can the shim detect these
> cases from the Anthropic side, which is all it sees?

This bears on the milestone: getting Claude Code working through the shim against real
Copilot. Tool calls are what Claude Code's agent loop runs on.

## Method

- **Code under test:** copilot-api v0.7.0 @ `0ea08fe` (= `npx copilot-api@latest`),
  clone in `.agent-work/copilot-api`. `lab/stream-translate/translate_stream.ts` (bun 1.3.3)
  imports the real `translateChunkToAnthropicEvents`. It drives the function the way
  `handler.ts:57-89` does: a fresh state per stream, skip on empty data, stop at `[DONE]`.
- **Corpus:** 18 hand-built chunk streams (`scenarios.py`). Each one records the turn it
  is meant to produce: text plus Bash tool calls `echo MARK_A` / `echo MARK_B`.
  - Controls: plain text; one tool; two tools in sequence; two tools in one chunk.
  - Shapes other OpenAI-compatible backends are known to emit: interleaved
    parallel-call arguments; tool call in `choices[1]`; repeated `finish_reason`; no
    `finish_reason`; id before name; id repeated on every chunk; same index reused;
    `choices: []` first/last chunks; `length` mid-arguments; `content_filter`.
- **Check 1 (`check.py`):**
  - Grammar check against the Anthropic streaming protocol: block start → deltas to the
    open block → stop, sequential indices, exactly one `message_delta` and one
    `message_stop`.
  - Fidelity check: rebuild the message the way an SDK accumulator does, then compare text,
    tool ids and parsed inputs.
- **Check 2, end to end (`run_one.sh`, `fake_upstream.py`):** real Claude Code 2.1.321 in
  `-p` mode, `--allowedTools Bash`, throwaway HOME, no credentials or network.
  - The fake upstream answers the main tool turn with the translated event stream, sent
    as hono's `streamSSE` would send it, then ends the connection. It answers every later
    turn with "done.".
  - Recorded per run: which `tool_result`s Claude Code sent back (that is, which tools it
    ran), what it replayed as the assistant turn, retries, exit code, final result.
- **Check 3 (`sse_lint.py`):** an incremental lint prototype the shim could run in its
  relay loop, tested on the same 18 translated streams split into 97-byte reads.
- **Reproduce:** `lab/stream-translate/run_all.sh .agent-work/copilot-api <claude-linux-x64 binary> .agent-work/stream`
  (≈25 s). Raw data: `events.json`, `check_results.json`, `e2e_results.json`,
  `lint_results.json`.

## Results

Of the 18 shapes, 8 come out of copilot-api broken. With real Claude Code: 3 fail the turn
on a stream that was complete (s06, s14, s09); 2 lose or garble tool input (s05, s15); s10
(a real upstream cut) is retried correctly; s08 is harmless; s12 is invisible.

| scenario | copilot-api output (check 1) | Claude Code 2.1.321 (check 2) | lint |
|---|---|---|---|
| s01–s04, s07, s13, s16, s17, s18 (controls, tools in `choices[1]` in their own chunks, index reused with new id, text after tool, `choices: []` chunks) | valid | all tools ran (1 or 2), exit 0 | clean |
| **s05** parallel calls whose argument deltas interleave | `input_json_delta` for block 1 after its `content_block_stop` | **tool B dropped from the turn; tool A runs with `{}` → `InputValidationError: command is missing`**, exit 0 (the model sees an error) | flagged |
| **s06** text in `choices[0]` and tool call in `choices[1]` of the same chunk | tool call dropped (only `choices[0]` is read, `stream-translation.ts:30`); `stop_reason: tool_use` with no tool block | **"The model's tool call could not be parsed (retry also failed)"**, 3 requests, exit 1 | flagged |
| **s14** id in one chunk, function name in the next | no `content_block_start` (needs id and name together, line 95); arguments dropped | same as s06: 3 requests, exit 1 | flagged |
| **s15** id+name repeated on every argument chunk | 4 `tool_use` blocks with the same id, each holding one fragment | only the first fragment is kept → `could not be parsed as JSON`, then `{}` → `command is missing` | flagged |
| s09 `[DONE]` with no `finish_reason` | no block stop, no `message_delta`/`message_stop` | "API Error: Connection lost mid-response", 4 requests (3 retries), exit 1 | flagged |
| s10 upstream cut mid-arguments (control for s09) | same as s09 | same as s09 (correct: it *was* cut) | flagged |
| s08 `finish_reason` repeated in a trailing usage chunk | `message_delta` + `message_stop` emitted twice | harmless: tool ran, exit 0 | flagged (info) |
| s11 `length` mid-arguments | `max_tokens` + partial JSON (same as the native API) | tool error with the raw fragment, model can recover | clean |
| s12 `content_filter` | mapped to `end_turn` (`utils.ts:13`) | **cut-off answer "Here is part of the ans" reported as success**, exit 0 | not detectable |

Notes:

- **s05 is the costly one:** a tool call is lost without any message, and the other one
  fails. Claude Code issues parallel tool calls all the time (Read×N, Grep+Glob). Whether
  Copilot interleaves argument deltas across `tool_calls` indices for Claude is
  **unconfirmed**. OpenAI's own streams finish one call before starting the next, which
  s03 shows works.
- **s06 is a known open question upstream.** copilot-api's non-stream path merges content
  from *all* choices (`non-stream-translation.ts:284-292`), which suggests Copilot does
  split Claude replies across choices there. A review on closed copilot-api PR #152 calls
  the streaming case hypothetical. CLIProxyAPI PR #2245 fixed the same class of bug
  (tool-call state keyed by `tool_calls.index` alone, merged across choices) for another
  backend.
- **s09:** Claude Code's retry is the right response for a real cut (s10). For s09 it
  spends 4 identical requests, which under token billing are 4 paid prompts, on a stream
  that was actually complete.
- **Lint prototype:** it flags all 7 structural defects (s05, s06, s08, s09, s10, s14,
  s15). On the 10 well-formed streams (9 clean shapes plus s11) it gives 0 false
  positives. s12 is not detectable without the OpenAI chunks. Cost is 13 µs per event in
  pure Python (5000-delta, 716 KB stream: 66 ms in total, spread over the stream), with
  no buffering.

What this does not show: which of these shapes real Copilot sends for Claude 5.x. Without a
Copilot login that can't be observed. All counts above are per hand-built shape, not
traffic rates.

## Sources

- copilot-api v0.7.0 @ `0ea08fe`:
  - `src/routes/messages/stream-translation.ts`: `choices[0]` only (l.30), tool start
    requires `id && function.name` (l.95), no guard after `finish_reason` (l.145).
  - `src/routes/messages/utils.ts` (`content_filter` → `end_turn`).
  - `handler.ts` stream loop; `non-stream-translation.ts:284-292` (merges all choices).
  - https://github.com/ericc-ch/copilot-api
- copilot-api PR #152 (closed), review note on `chunk.choices[0]` in streaming:
  https://github.com/ericc-ch/copilot-api/pull/152
- CLIProxyAPI PR #2245, tool-call state keyed by (choice index, tool index):
  https://gh.r-mj.com/router-for-me/CLIProxyAPI/pull/2245
- coproxy 0.6.0 `src/anthropic/stream.rs`, another OpenAI→Anthropic stream converter. It
  also reads `choices[0]` only but defers `message_delta`/`message_stop` until a usage-only
  chunk: https://docs.rs/crate/coproxy/0.6.0/source/src/anthropic/stream.rs
- Anthropic streaming event grammar: https://docs.claude.com/en/docs/build-with-claude/streaming

## What the planner should do next

1. **First logged-in session: capture before fixing.** Run copilot-api with `--verbose`
   (it logs every `Copilot raw stream event`). Have Claude Code issue parallel reads
   ("read these 3 files"). Record whether Copilot (a) interleaves argument deltas across
   `tool_calls` indices, (b) uses `choices[1]`, or (c) splits id and name. That one
   capture settles which of s05/s06/s14/s15 are real. Add it as a credential-free fixture.
2. **Small shim change, low risk:** add `sse_lint.py`'s `SSELint` to `server.py`'s relay
   loop (`_relay`, the chunked branch) for `/v1/messages`. Log one WARNING per defective
   stream, naming the defect. It changes no bytes and gives ongoing evidence from normal
   use. Worth a PR independently of step 1.
3. **Only if step 1 shows s05 or s06:** a repair is possible in the shim, since s05 can be
   re-sequenced from the Anthropic side by buffering tool blocks until `message_stop`. The
   better fix is upstream (copilot-api: key tool state by (choice, index), allow a
   deferred name, close blocks only at finish). Or take the native Messages route from
   thinking-fields, which avoids the chunk translation altogether.
4. `content_filter` → `end_turn` (s12) is worth one line in docs/SETUP.md troubleshooting:
   a reply that stops mid-sentence and is reported as complete may be Copilot's filter.

## Files

- `lab/stream-translate/scenarios.py`: the 18 chunk streams and the turn each should produce.
- `lab/stream-translate/translate_stream.ts`: driver for copilot-api's own stream translator.
- `lab/stream-translate/check.py`: grammar and fidelity checks → `check_results.json`.
- `lab/stream-translate/fake_upstream.py`, `run_one.sh`, `summarize.py`: end-to-end runs with
  Claude Code → `e2e_results.json`.
- `lab/stream-translate/sse_lint.py`: shim-side lint prototype → `lint_results.json`.
- `lab/stream-translate/run_all.sh`: all of the above.
