# Does Claude Code recover when Copilot's prompt-token cap rejects a request?

## Question

Copilot caps each model's prompt below the model's real context window. The cap is
`capabilities.limits.max_prompt_tokens` in `/models`, which copilot-api 0.7.0 types in
`src/services/copilot/get-models.ts:23`. Claude Code doesn't know this cap. It plans
auto-compaction against its own idea of the window.

When a request goes over the cap, does Claude Code 2.1.321 compact and carry on, or does
the session break? Does the answer depend on the shape of the error copilot-api forwards,
and on the usage numbers it reports? Which mitigations work: an env var, or a shim change?

## Method

- **Client:** the real Claude Code 2.1.321 native binary (`.agent-work/cc/node_modules`,
  the same one prefill-source and cache-prefix used).
  - Headless turns: `-p`, then `-p --continue`, 22 turns per configuration (30 for H).
  - Throwaway `HOME`, `ANTHROPIC_BASE_URL=127.0.0.1`, no credentials, no network.
  - `ANTHROPIC_MODEL=claude-opus-5.5` (the Copilot-style id).
- **Upstream:** `lab/compact-cap/fake_copilot.py`. It counts a *virtual* prompt size,
  `bytes/4 + PAD` per `<<PAD>>` marker. Each turn's prompt carries one marker, so a session
  grows by about 10k tokens per turn (40k in H).
  - Above `CAP`, it rejects the request with a 400.
  - It treats a request containing Claude Code's compaction prompt ("detailed summary of the
    conversation") as a compaction call and answers it with a short `<summary>`.
- **Error format (`ERRFMT`):**
  - `copilot`: what copilot-api forwards. `forwardError` (`src/lib/error.ts`) wraps
    Copilot's JSON *as a string* in
    `{"error":{"message":"<text>","type":"error"}}`. Here the text is Copilot's
    `{"error":{"message":"prompt token count of N exceeds the limit of M","code":"model_max_prompt_tokens_exceeded"}}`.
    That inner message and code come from community reports of real Copilot errors. I
    couldn't check them against a live endpoint here.
  - `anthropic`: `invalid_request_error` with `prompt is too long: N tokens > M maximum`.
- **Usage reporting (`USAGE`):**
  - `delta`: copilot-api's stream translation (`stream-translation.ts:44,161`) when
    Copilot's first chunk carries no usage. `message_start` gets `input_tokens=0` and
    `message_delta` gets N.
  - `none`: usage is lost entirely, with 0 in both events. This happens if Copilot sends
    usage only in a `choices: []` chunk: copilot-api returns early on empty `choices`
    (line 26) and never sets `stream_options.include_usage`. Real Copilot behaviour wasn't
    checked here, so I tested both.
- **Cap:** 128 000, a typical Copilot `max_prompt_tokens` for Claude models. The real value
  for each model should be read from `/models`.
- **Scripts:** `lab/compact-cap/run.sh`, `lab/compact-cap/summarize.py`. Raw per-turn
  output is in `lab/compact-cap/results.txt`.

## Results

| cfg | cap | error format | usage | env | failed turns / 22 | 400s | compactions | largest OK prompt |
|---|---|---|---|---|---|---|---|---|
| A | none | – | delta | – | 0 | 0 | **0** (up to 232k) | 232k |
| H | none, 40k/turn | – | delta | – | 0 / 30 | 0 | 1, at **974k** | 972k |
| **B** | 128k | **copilot** | delta | – | **11** (t12–t22, every turn after the first overflow) | 66 | 0 | 122k |
| D | 128k | copilot | none | – | 11 | 66 | 0 | 122k |
| **C** | 128k | **anthropic** | delta | – | **0** | 2 | 2 (reactive, t12, t18) | 122k |
| G | 128k | anthropic | none | – | 0 | 2 | 2 (reactive) | 122k |
| **E** | 128k | copilot | delta | `CLAUDE_CODE_AUTO_COMPACT_WINDOW=128000` | **0** | 0 | 2 (proactive, at 103k) | 102k |
| F | 128k | copilot | none | `CLAUDE_CODE_AUTO_COMPACT_WINDOW=128000` | 11 | 66 | 0 | 122k |

### Findings

1. **Claude Code assumes a ~1M window for `claude-opus-5.5` and never compacts before
   Copilot's cap** (A, H). It first auto-compacted at 974k. With any Copilot cap below that,
   proactive compaction never fires on its own.
2. **With copilot-api's error format, the first overflow breaks the session for good**
   (B).
   - Claude Code 2.1.321 detects overflow only by message text: `prompt is too long` or
     `input is too long for requested model`, with the regex
     `prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)` (strings from the binary).
     Copilot's "prompt token count of N exceeds the limit of M" matches neither, so Claude
     Code treats it as a plain 400. It shows `API Error: 400 {...}` and keeps the failed
     user turn in history.
   - Every later turn is larger and fails too: 11/11 turns failed.
   - Each failed turn sends **6** requests to Copilot: 3 identical attempts, then 3
     attempts of a 25-message variant about 10k larger.
   - **A manual `/compact` doesn't recover the session either.** The compaction request
     carries the whole 234k history and is rejected 8 times. The next normal turn still
     fails (`B-manual` in results.txt). The user's only way out is to start a new session.
3. **The same cap with Anthropic's error text recovers fully** (C).
   - Claude Code catches the 400, compacts reactively, and retries the turn. The sequence is
     132k/400 → a compaction call of 73k (it trims the history it sends for summarising) →
     72k OK.
   - 0 failed turns, and only 1 extra rejected request per overflow instead of 6 per turn
     forever.
   - **This works with usage lost too** (G), because reactive compaction doesn't depend on
     reported usage.
4. **`CLAUDE_CODE_AUTO_COMPACT_WINDOW` works only if usage reaches Claude Code** (E vs F).
   - With `delta` usage, Claude Code compacts proactively at about 103k (≈80% of 128k), with
     0 rejections.
   - With usage lost, it has no token count, never compacts, and fails exactly like B.
5. Reporting usage in `message_delta` only (copilot-api's behaviour when the first chunk
   lacks usage) is enough for Claude Code's proactive accounting (E).

## What the planner should do next

- **Shim change (recommended, small).** At `copilot_shim/server.py:118` the shim already
  buffers every ≥400 body.
  - When the status is 400 and the body (including the stringified inner JSON) contains
    `model_max_prompt_tokens_exceeded` or `prompt token count of N exceeds the limit of M`,
    replace it with
    `{"type":"error","error":{"type":"invalid_request_error","message":"prompt is too long: N tokens > M maximum"}}`.
  - Fix Content-Length to match the new body, and log the original.
  - This turns a session-breaking failure into one reactive compaction (C/G), whatever the
    usage reporting.
  - Test it with the existing fake upstream: a canned copilot-style 400 should come out in
    Anthropic form. Unrelated 400s should pass through byte-identical.
- **Docs (SETUP.md).** Recommend `CLAUDE_CODE_AUTO_COMPACT_WINDOW=<max_prompt_tokens>` as a
  belt-and-braces setting. It avoids the extra rejected request (E). Note that it does
  nothing if usage doesn't come through (F).
  - `copilot_shim.probe` could print each Claude model's `max_prompt_tokens` from `/models`,
    so users know which value to set.
- **Still to confirm on a real Copilot account (owner, when convenient):**
  - the exact error text and code for an over-cap prompt;
  - whether streamed responses carry usage in a `choices`-bearing chunk (delta) or only in
    a `choices: []` chunk (none);
  - the real `max_prompt_tokens` for `claude-opus-5.5`.

  The shim rewrite should match loosely, on either the code or the phrase, because the
  exact text isn't confirmed.

## Sources

- copilot-api 0.7.0 (`0ea08fe`): `src/lib/error.ts` (forwardError),
  `src/routes/messages/stream-translation.ts` lines 26, 44, 161,
  `src/services/copilot/get-models.ts:21-23`.
- Claude Code 2.1.321 binary strings: the overflow matchers above, and the env vars
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` and
  `CLAUDE_CODE_MAX_CONTEXT_TOKENS`. Only the first was tested.
- Earlier lab notes: cache-prefix and prefill-source, which used the same client binary.
