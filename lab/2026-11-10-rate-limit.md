# 2026-11-10: What does Claude Code do when Copilot rate-limits it behind copilot-api?

## Question

The milestone is getting Claude Code working through the shim against real Copilot. Copilot
rate-limits accounts with short burst limits and with long session/weekly quotas. Earlier lab
notes covered request shapes (prefill, translation, caching, compaction, tools, thinking).
None of them looked at **errors on the way back**. This note asks:

> When Copilot answers 429 (or 5xx), copilot-api 0.7.0 forwards it through the shim to Claude
> Code 2.1.321. How does Claude Code retry? How long does a turn hang before it fails? Does it
> recover from short limits? What should the shim change, if anything?

## Method

1. **copilot-api source** (v0.7.0, `0ea08fe`, `.agent-work/copilot-api`).
   `src/lib/error.ts` `forwardError` reads Copilot's error body as text. It returns
   `c.json({error: {message: <that text>, type: "error"}}, status)`, which is a **new
   response**. Copilot's response headers, including any `retry-after` or `x-ratelimit-*`, are
   dropped. copilot-api's own `--rate-limit` rejection (`src/lib/rate-limit.ts`) has the same
   shape, with message `{"message":"Rate limit exceeded"}` and no header.
   The shim (`copilot_shim/server.py:118`) relays status, headers and body of every ≥400 response
   unchanged. It therefore cannot pass on a header that copilot-api already removed.
2. **Fake upstream** `lab/rate-limit/fake_copilot.py` stands in for copilot-api. It answers
   `/v1/messages` with errors in copilot-api's exact `forwardError` shape, or in the Anthropic
   shape (`FMT=anthropic`). The failure plan is one of: the first K requests, every request
   during the first W seconds, or every request. `retry-after` and `x-should-retry` headers are
   optional. The default message is a 5-hour session-limit text,
   `"... Please wait 5 hours 52 minutes ..."`, with code `user_global_rate_limited:pro_plus`.
   **Assumption:** this text is modelled on community reports. I found no official
   documentation of Copilot's 429 body or headers (see Sources), and I made no real Copilot
   calls.
3. **Real Claude Code 2.1.321** (native binary
   `.agent-work/cc/node_modules/@anthropic-ai/claude-code-linux-x64/claude`) ran headless:
   `-p "say ok"`, throwaway HOME, `ANTHROPIC_BASE_URL` set to the fake, no credentials, no
   network. `run_one.sh` runs one scenario, `matrix.sh` runs all 16 in parallel, and
   `summarize.py` prints the table. `threshold.sh` bisects the `retry-after` cutoff.
4. **Static check** of the bundle strings (`.agent-work/cc/strings.txt`) to explain the
   numbers:
   - the backoff is `uL(attempt)`: 500 ms·2ⁿ, capped at 32 s, ±25 % jitter, raised to
     `retry-after` when one is present;
   - retryable statuses are 408, 409, 429 and ≥500;
   - `x-should-retry: true/false` overrides the status.

## Results

### Retry matrix (one headless turn each; raw table in `lab/rate-limit/results-matrix.txt`)

| cfg | upstream behaviour | requests | outcome | wall |
|---|---|---|---|---|
| k1 / k3 / k6 | first 1 / 3 / 6 requests 429 | 2 / 4 / 7 | **ok** | 4.9 / 7.8 / 37.7 s |
| D | 429 for 20 s | 7 | **ok** | 37 s |
| E | 429 for 60 s | 8 | **ok** | 75 s |
| K | 429 for 180 s | 11 | **fail** (budget exhausted) | 184 s |
| A | 429 always (copilot-api shape, no header) | 11 | fail | **178 s** |
| G | same, Anthropic error shape | 11 | fail | 176 s |
| H / I | 503 / 500 always | 11 / 11 | fail | 177 / 180 s |
| J | 429 always, `CLAUDE_CODE_MAX_RETRIES=3` | 4 | fail | 8.6 s |
| C | 429 always + `retry-after: 30` | 11 | fail | 330 s (30 s gaps, then backoff ≈ 35 s) |
| F | 429 for 60 s + `retry-after: 60` | **2** | **ok** | 64 s (one 60.0 s gap) |
| B | 429 always + `retry-after: 21120` (5 h 52 m) | **1** | fail at once | 4.4 s |
| M | 429 always + `x-should-retry: false` | 1 | fail at once | 4.4 s |
| N | 503 always + `x-should-retry: false` | 1 | fail at once | 4.4 s |

- **Default budget:** 10 retries, so 11 requests, over about 178 s. The gaps were
  0.5, 1.1, 2.4, 4.8, 9.2, 16.7 s, then 32–40 s each.
  - Every limit that clears within about 3 minutes is absorbed with no user-visible error.
  - Every limit that lasts longer costs the user **3 minutes of hanging and 11 requests**
    before the error appears. With a 5-hour quota that wait is pointless.
- Each retry is sent with `x-stainless-retry-count: 0` (11/11 in A). Claude Code runs its own
  retry loop, so upstream (Copilot, copilot-api, the shim) can't tell retries from new
  requests.
- **Error format matters only for display, not for retrying.**
  - copilot-api shape: `API Error: Request rejected (429) · {"error": {"message": "Sorry,
    you've exceeded …", "code": "user_global_rate_limited:pro_plus"}}`, which shows raw
    nested JSON.
  - Anthropic shape: `API Error: Request rejected (429) · Sorry, you've exceeded your 5 hour
    session limits. Please wait 5 hours 52 minutes before trying again.`
  - 5xx errors add "This is a server-side issue … check your inference gateway (127.0.0.1:…)".

### `retry-after` cutoff (`lab/rate-limit/results-threshold.txt`)

| retry-after (s) | 45 | 59 | 60 | 60.5 | **61** | 120 | 300 | 600 | 1800 | 3600 | 7200 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Claude Code | waits | waits | waits | waits | **fails at once** | fails | fails | fails | fails | fails | fails |

- "Waits" means the run was still sleeping after the first request when the 20 s timeout hit.
- "Fails at once" means exit 1 after 1 request, in 1.4–2.1 s.
- So Claude Code honours a `retry-after` of **≤ 60 s** and treats anything longer as "give up
  now and show the message".
- For a short limit the header turns 8 requests / 75 s (E) into 2 requests / 64 s (F).
- For a session quota the header turns 11 requests / 178 s (A) into 1 request / ~2 s (B).

### Shim prototype

`lab/rate-limit/retry_hint.py` `rewrite_429(body)` does three things:

1. It unwraps copilot-api's double-encoded body.
2. It parses a wait (`wait|try again in|resets in` + `N days/hours/minutes/seconds`) into
   `retry-after`. If there is no wait but the code is a quota code
   (`user_global_rate_limited`, `user_weekly_rate_limited`, `quota_exceeded`), it sets
   `x-should-retry: false`.
3. It re-emits the error as Anthropic `rate_limit_error`.

The self-check passes 6/6 cases, including copilot-api's own `Rate limit exceeded` and
non-JSON bodies. Those last two are left without a header and keep the default backoff.

The headers the prototype emits are exactly the ones configured in cases B, C, F and M above.
Their effect on Claude Code is therefore the measured one. I did not wire the prototype into
the shim in this session.

## Limits

- **Headless `-p` only.** The interactive TUI may show a retry countdown, but the retry policy
  is the same code (`uL`, `shouldRetry`). Background helper queries (title, side queries) have
  their own handling (`NHt` / `x-cc-retry-after-too-long` in the bundle), which I did not
  measure.
- **Copilot's real 429** body text, codes and headers are unverified. The parser only helps if
  the real message contains a wait phrase. A session with a real login should capture one 429
  (body + headers, straight from api.githubcopilot.com and through copilot-api) before the
  regex is relied on.
- I did not measure whether rejected retries count against Copilot quotas or abuse heuristics.

## What the planner should do next

1. **Shim change (small, no owner decision needed).** In `server.py` `_relay`'s ≥400 branch
   (line 118), for 429 from `/v1/messages`, apply `rewrite_429`: Anthropic error shape, plus
   `retry-after` / `x-should-retry` recovered from the text. Tests can reuse
   `tests/fake_upstream.py` with the copilot-api error shape.
   - Expected effect, measured here: a quota hit fails in ~2 s with a readable message instead
     of 178 s and 11 requests; short limits retry once at the right time.
2. **Capture one real Copilot 429** in a session with a login, as a checklist item next to the
   login checklist from the thinking-fields note. This confirms the message format and shows
   whether Copilot sends `retry-after` at all. If it does, a copilot-api fix (keep upstream
   headers in `forwardError`) is the cleaner upstream fix; check its issues/PRs first.
3. **Document in docs/SETUP.md:**
   - `CLAUDE_CODE_MAX_RETRIES` (honoured: 3 → 4 requests, 8.6 s) for users who prefer fast
     failure;
   - copilot-api's `--rate-limit N --wait`, which makes copilot-api delay requests itself and
     never return a 429 to Claude Code.

## Sources

- copilot-api v0.7.0 `src/lib/error.ts` (forwardError), `src/lib/rate-limit.ts`:
  https://github.com/ericc-ch/copilot-api
- Claude Code 2.1.321 bundle strings (retry backoff `uL`, `shouldRetry`, `x-cc-retry-after-too-long`), local only.
- Copilot SDK rate-limit codes (`user_global_rate_limited`, `user_weekly_rate_limited`), secondary source:
  https://instagit.com/github/copilot-sdk/copilot-sdk-api-rate-limits/
- GitHub Models 429 example with `retry-after: 24367` (a different product; it shows that GitHub
  does send long `retry-after` values): https://dev.to/devactivity/mastering-github-models-api-rate-limits-quotas-and-software-engineering-quality-4fk1
- GitHub docs, handling API rate limits: https://docs.github.com/en/copilot/copilot-chat-cookbook/debugging-errors/handling-api-rate-limits
