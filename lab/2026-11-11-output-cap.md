# Claude Code's `max_tokens: 128000` against Copilot's output-token cap

## Question

Claude Code 2.1.321 asks for `max_tokens: 128000` on every Opus/Sonnet main-loop request
(thinking-fields lab: 91/97 real bodies). copilot-api 0.7.0 forwards the value unchanged
(`non-stream-translation.ts:38`). Copilot's `/models` lists a much lower
`capabilities.limits.max_output_tokens` for Claude models: 32 000 or 64 000 in the public
listings below. thinking-fields left this as an open item ("check `max_tokens: 128000`
against Copilot's `max_output_tokens`").

Real Copilot does one of two things with an over-cap `max_tokens`. It either rejects the
request or clamps it silently. Which one is unknown, because this lab has no Copilot login.

For each behaviour:

- Does a Claude Code session still work?
- How much output can one turn produce?
- Which mitigation works: the `CLAUDE_CODE_MAX_OUTPUT_TOKENS` env var, or a shim-side clamp?

## Method

- **Client:** the real Claude Code 2.1.321 native binary (`.agent-work/cc`, the same one as
  the earlier labs).
  - Headless `-p` turns, with `--continue` for multi-turn cases.
  - Throwaway `HOME`, base URL 127.0.0.1, no credentials, no network.
  - `ANTHROPIC_MODEL=claude-opus-5.5` unless stated otherwise.
- **Upstream:** `lab/output-cap/fake_copilot.py`, which plays copilot-api's Anthropic side.
  `MODE` picks what happens when `max_tokens` is over `CAP`:
  - `pass`: no cap (control).
  - `reject-cp`: a 400 wrapped the way copilot-api's `forwardError` does it, with Copilot's
    JSON as a string. The inner text, "max_tokens is too large: X. This model supports at
    most CAP completion tokens.", is a guess (OpenAI-style) and isn't confirmed. Claude Code
    recognises none of it (see `reject-max`), so the exact wording doesn't change the
    outcome.
  - `reject-max`: Anthropic's own text, `max_tokens: X > CAP, which is the maximum allowed
    number of output tokens for this model`. Claude Code's binary contains this regex.
  - `reject-ctx`: Anthropic's `input length and \`max_tokens\` exceed context limit: I + X >
    L`. Claude Code parses this one (function `GOe` in the binary).
  - `clamp`: accepts the request, generates at most `min(max_tokens, CAP)` tokens, then
    ends with `stop_reason: max_tokens`.
- **Long answers.** A prompt containing `<<LONG k>>` makes the fake model "need" k output
  tokens. Each response delivers up to the allowed amount (as `usage.output_tokens`). When
  Claude Code sends its output-limit nudge ("Output token limit hit. Resume directly…"),
  the next response continues the remainder.
- **Shim prototype:** `lab/output-cap/proto_shim.py` runs the real `copilot_shim` server.
  It wraps `rewrite()` so that `max_tokens` above a per-model glob cap
  (`claude-*=32000`) is lowered to the cap, along with `thinking.budget_tokens` if
  present. The body is then re-serialised as usual.
- **Runs:** `lab/output-cap/matrix.py` runs 23 cases and writes `results.json` and
  `summary.txt`. The whole matrix was run twice with identical request counts and
  outcomes.

## Results

| case | upstream | `max_tokens` sent | output needed | `/v1/messages` requests | 400s | turn |
|---|---|---|---|---|---|---|
| pass-opus / pass-sonnet | none | 128000 | short | 1 | 0 | ok |
| pass-haiku45 | none | **32000** | short | 1 | 0 | ok |
| pass-long100k | none | 128000 | 100k | 1 | 0 | ok |
| pass-long400k | none | 128000 | 400k | 4 | 0 | ok (3 nudges) |
| **reject-cp32** | reject, copilot-api wrap | 128000 | short | **6** | **6** | **fails**: `API Error: 400 {"error": …}` |
| reject-cp64 | reject, cap 64k | 128000 | short | 6 | 6 | fails |
| **reject-max32** | reject, Anthropic `max_tokens: X > Y` text | 128000 | short | 6 | 6 | **fails** |
| reject-ctx32 | reject, Anthropic context-limit text | 128000 → 31000 | short | 2 | 1 | ok |
| reject-ctx32-long100k | same | 128000 → 31000, every request | 100k | 8 | 4 | ok |
| clamp32-long30k | clamp 32k | 128000 | 30k | 1 | 0 | ok |
| clamp32-long100k | clamp 32k | 128000 | 100k | 4 | 0 | ok (3 nudges) |
| **clamp32-long200k** | clamp 32k | 128000 | 200k | 4 | 0 | **fails** after 128k: "exceeded the **128000** output token maximum" |
| clamp32-long400k | clamp 32k | 128000 | 400k | 4 | 0 | fails |
| clamp64-long200k | clamp 64k | 128000 | 200k | 4 | 0 | ok |
| reject-cp32-env | reject | 32000 (env) | 4 turns incl. `/compact` | 5 | 0 | 4/4 ok |
| reject-cp32-env-sonnet | reject | 32000 (env) | short | 1 | 0 | ok |
| reject-cp32-env-long100k | reject | 32000 (env) | 100k | 4 | 0 | ok |
| clamp32-env-long100k | clamp 32k | 32000 (env) | 100k | 4 | 0 | ok |
| clamp32-env-long200k | clamp 32k | 32000 (env) | 200k | 4 | 0 | fails: "exceeded the **32000** output token maximum" |
| **shim-reject-cp32** | reject, via shim + clamp | 32000 (shim) | 4 turns incl. `/compact` | 5 | 0 | 4/4 ok |
| shim-reject-cp32-long100k | same | 32000 (shim) | 100k | 4 | 0 | ok |
| shim-reject-cp32-sonnet | same | 32000 (shim) | short | 1 | 0 | ok |

### Findings

1. **If Copilot rejects an over-cap `max_tokens`, Claude Code on Opus or Sonnet can't
   complete a single turn.**
   - Every main-loop request asks for 128 000, whatever the model's real cap (32k or 64k
     makes no difference).
   - Each turn sends 6 rejected requests: 3 identical ones, then 3 of a larger 87 KB
     variant, the same pattern compact-cap saw. Then the raw 400 is shown.
   - This doesn't depend on the error format. Anthropic's exact `max_tokens: X > Y` text is
     in Claude Code's binary, but only in a first-party error classifier. It triggers no
     retry with a smaller value.
   - Haiku 4.5 asks for 32 000, so it passes a 32k cap. A session would look as if only
     Haiku works.
2. **Only the "input length and `max_tokens` exceed context limit" text makes Claude Code
   adjust on its own.**
   - It retries with `L − I − 1000` (31 000 here), so the turn succeeds.
   - The adjustment isn't remembered. Every later request, including each output-limit
     continuation, first tries 128 000 again: 4 of 8 requests were rejected in the 100k case.
   - Rewriting the error into this format would work, but costs a wasted round trip per
     request. A request-side clamp is strictly better.
3. **If Copilot clamps silently, sessions work, but a turn's output ceiling drops 4×.**
   - Claude Code's output-limit recovery sends at most 3 continuation nudges, so 4 requests
     per turn.
   - The per-turn ceiling is therefore 4 × the effective cap: 512k natively, 256k at a 64k
     cap, and **128k at a 32k cap**. 100k-token answers still finish, while 200k ones fail.
   - The failure message names the wrong limit ("exceeded the 128000 output token maximum").
     Claude Code believes its own `max_tokens`.
   - Each continuation is a separate request carrying the whole history again, so it is
     billed again too.
4. **`CLAUDE_CODE_MAX_OUTPUT_TOKENS=<cap>` fixes both behaviours with no code change.**
   - It applied to every request seen: main loop on Opus and Sonnet, the `/compact` call,
     and the continuations.
   - It gave 0 rejected requests in 4/4 turns.
   - With a clamping upstream, the ceiling is the same as without it (4 × cap), and the
     error message then names the true cap.
5. **The shim-side clamp prototype matches the env var without user configuration** (0
   400s, 4/4 turns).
   - The shim's log shows `action=clamped` alongside the existing `system_folded`.
   - Its only downside is the misleading "128000" text in Claude Code's give-up message
     (finding 3), because Claude Code never learns the lower value.

### Not covered

- Real Copilot's behaviour and error text for an over-cap `max_tokens`. One weak hint
  points toward clamping or acceptance: the owner's original 400s were the prefill error,
  not a `max_tokens` one, even though Claude Code sends 128000. Validation order is unknown,
  though.
- Tool calls cut at the cap (a large `Write`): stream-translate s11 covers mid-argument
  `length` cut-offs.
- Thinking. copilot-api drops it (thinking-fields), but the native Messages route would
  carry it, and there `budget_tokens < max_tokens` must still hold after clamping. The
  prototype lowers the budget for this; it wasn't exercised here.
- Real per-model cap values. The `/v1/models` that copilot-api 0.7.0 serves drops
  `capabilities` (`src/routes/models/route.ts`), so the shim can't learn the caps from
  copilot-api. They need a flag, or a probe run against the Copilot token.

## What the planner should do next

1. **Docs, now (no risk).** Add `CLAUDE_CODE_MAX_OUTPUT_TOKENS=32000` to the SETUP.md env
   block and HANDOVER step 1.
   - Use the smallest `max_output_tokens` among the Claude models in use.
   - It is harmless if Copilot turns out to clamp, and essential if it rejects.
2. **Shim (small, recommended).** Add `--max-output GLOB=N`, repeatable, with a default
   such as `claude-*=32000`. The clamp runs after `--map` in `rewrite()` and also lowers
   `thinking.budget_tokens`.
   - `proto_shim.py` is the 25-line reference.
   - Test it with the fake upstream: a 128000 body comes out at the cap, and a body at or
     below the cap passes through byte-identical.
3. **Probe.** Have `copilot_shim.probe` print `max_output_tokens` and `max_prompt_tokens`
   per Claude model. That needs the Copilot `/models` response, which copilot-api's
   `/v1/models` strips. On first login, also send one `max_tokens: 128000` request per
   model and record whether it gets a 400 (with the exact text) or a 200 with
   `finish_reason: length` at the cap. That settles which half of this table applies.
4. **Don't** rewrite over-cap 400s into the "context limit" format as the fix. It works,
   but doubles the requests (finding 2). The request-side clamp avoids the rejection
   entirely.

## Sources

- Claude Code 2.1.321 binary strings:
  - the output-limit nudges: "Output token limit hit. Resume directly…";
  - `max_output_tokens_recovery`;
  - `/input length and \`max_tokens\` exceed context limit: (\d+) \+ (\d+) > (\d+)/`
    (function `GOe`);
  - the first-party classifier list containing
    `^max_tokens: \d+ > \d+, which is the maximum allowed number of output tokens for this model$`;
  - `CLAUDE_CODE_MAX_OUTPUT_TOKENS`.
- copilot-api 0.7.0: `src/routes/messages/non-stream-translation.ts:38` (`max_tokens`
  forwarded), `src/routes/chat-completions/handler.ts:42-47` (fills in
  `max_output_tokens` only when absent), `src/routes/models/route.ts` (capabilities
  dropped), `src/lib/error.ts` (forwardError).
- Copilot `/models` output caps (third-party, unverified):
  - models.dev issue on Copilot 1M variants, 2026-06-05: Claude 32 000–64 000 output:
    https://github.com/anomalyco/models.dev/issues/2021
  - Copilot `/models` field names (`max_output_tokens` and others):
    https://git.secluded.site/zed/commit/13ad175c8de2dcf28c815f91b51baa47a0444a83
  - catwalk Copilot provider configs:
    https://git.secluded.site/catwalk/blob/v0.39.5/internal/providers/configs/copilot.json
- Claude Code issues with over-limit `max_tokens` against Anthropic's own API (the
  `max_tokens: X > Y` error is fatal there too):
  https://claudeissues.com/issue/24455-bug-api-error-max-tokens-250001-128000-and-thinking-budget-tokens-error-on-opus
- Earlier lab notes: thinking-fields (`max_tokens` values in real bodies), compact-cap (the
  6-request retry pattern), stream-translate (s11, `length` mid-arguments).
