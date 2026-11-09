# 2026-11-09: Does the shim's system-message fold break Copilot's prompt cache?

## Question

The copilot-translate lab found one cost risk: the shim flips some bodies to
`X-Initiator: user`, which might use up premium requests. Since **2026-06-01**, GitHub
bills every Copilot plan by tokens, in GitHub AI Credits (1 credit = $0.01). The only
exception is annual Pro/Pro+ plans that still run on the legacy request-based terms. Under
token billing the cost driver is cached versus uncached input. For `claude-opus-5.5`,
cached input costs $0.20 per 1M tokens and uncached input costs $4.00, a factor of 20.

The shim moves every `role:"system"` message into the top-level `system` field
(`_fold_system_messages`). That field comes before the whole conversation in the prompt.
So the question is:

> In a real Claude Code session, does folding change the prompt prefix between
> consecutive requests? If so, how much cache, and how much money, does it cost compared
> with an append-only alternative?

## Method

1. **Capture real request shapes.**
   - Claude Code **2.1.321** (npm `@anthropic-ai/claude-code`, the native linux-x64
     binary) ran against `lab/cache-prefix/capture_server.py`. That server is a fake
     Anthropic endpoint on 127.0.0.1 that records each body and answers with canned SSE.
     It has no model, uses no credentials, and the run used a throwaway `HOME`.
   - `lab/cache-prefix/capture.sh` drives a 6-turn `-p --continue` session: two `Read`
     tool calls, then a Bash `cd sub` that changes the working directory.
   - The run produced 9 requests. Their shapes, with roles, block types and lengths only
     and no prompt text, are in `capture_shape.json`.
2. **Push each request through the chain:**
   - shim transform;
   - copilot-api 0.7.0's real `translateToOpenAI`, at `0ea08feb` via bun 1.3.3, reusing
     `lab/copilot-translate/translate.ts`;
   - the rendered prompt (tools, then messages).
3. **Cache model.** Cached tokens for request *i* are the longest common prefix with any
   earlier request in the session (tokens ≈ chars/4). This is an ideal prefix cache.
   - An OpenAI-style rule (≥1024 tokens, 128-token steps) gives the same picture (in
     `capture_result.json`).
   - Copilot's real caching rule for Claude is not documented. copilot-api drops every
     `cache_control`, but it relays the `cached_tokens` that Copilot reports, so Copilot
     does cache prefixes somehow.
4. **Transforms compared:**
   - `off`: unchanged.
   - `strip`: shim on `main` @ 960daf7.
   - `inplace`: a lab prototype in `analyze.py`. Each `role:"system"` message becomes a
     `role:"user"` message where it stands, then the prefill is stripped as before.
5. **Scaled simulation.**
   - Sessions of 120 requests grown from the captured first request (8k tokens of tools
     plus 3.7k of system and env). 80% of steps are tool rounds with results of 200–6000
     tokens; the rest are new prompts.
   - 0, 1, 3 or 10 sticky `# Environment update` system messages are inserted, the same
     way the capture shows them.
   - 5 sessions per setting, seed 1, priced at Opus 5.5 rates.
   - Run time: 7m45s, mostly the pure-Python prefix compare.

Commands:

```bash
lab/cache-prefix/capture.sh <claude binary> .agent-work/cc/run2 .agent-work/cc/run2.jsonl
python3 lab/cache-prefix/analyze.py .agent-work/copilot-api capture .agent-work/cc/run2.jsonl
python3 lab/cache-prefix/analyze.py .agent-work/copilot-api sim .agent-work/cc/run2.jsonl 1
```

## Results

### 1. What Claude Code 2.1.321 actually sends (capture)

- **The env block stays in place.** Request 0 is `[user, system]`, where the system
  message holds 9183 chars: env, skills list and date. From request 1 on, that same
  message stays at **index 1** of the history, byte-identical (one distinct value across
  all 9 requests).
  - So the trailing `role:"system"` that HANDOVER and `test_system_role.py` describe is
    only trailing on the first request. After that it sits in the middle of the history.
- **A `cd` adds a new system message that also stays.** After the working directory
  changes, Claude Code appends a new `role:"system"` message,
  `# Environment update\n - Primary working directory: … (was …)`, at the end of that
  request (request 6, index 14). It stays in every later request.
  - Each new `claude --continue` process that starts in a different directory adds
    another one (request 7, index 17).
  - The same messages appear in interactive sessions. This lab session's own transcript
    receives them after each `cd`.
- **System field.** `system[0]` is `x-anthropic-billing-header: cc_version=2.1.321.4ea;
  cc_entrypoint=sdk-cli;` and stays constant within the session. `cache_control` sits on
  the system blocks and on the last message.

### 2. The capture through the chain (9 requests, ~12k tokens each)

| transform | ends on assistant (prefill 400) | cache hit | cached tokens at the `cd` requests (6, 7) |
|---|---:|---:|---|
| off | 3 / 9 | 0.886 | 12216/12377, 12377/12465 |
| strip (main) | **0** | 0.874 | **11577/12361, 11637/12441** |
| inplace (prototype) | **0** | 0.886 | 12215/12375, 12375/12461 |

- With `strip`, every request that brings in a new system message loses the cache for
  the whole conversation after the `system` field. Tools and the original system field
  stay cached.
- The captured conversation is only about 700 tokens long, so the loss here is small.
  The number that matters is the conversation length at the moment of the update.

### 3. Scaled sessions (120 requests, mean prompt ~170k tokens; mean of 5 sessions)

| env updates per session | off: hit / $ input | **strip: hit / $ input** | inplace: hit / $ input |
|---:|---|---|---|
| 0  | 0.984 / $5.46 (fails: 5 req) | 0.984 / $5.46 | 0.984 / $5.46 |
| 1  | 0.984 / $5.47 (fails: 10)    | 0.979 / **$5.84** | 0.984 / $5.47 |
| 3  | 0.984 / $5.12 (fails: 20)    | 0.962 / **$6.74** | 0.984 / $5.11 |
| 10 | 0.984 / $5.37 (fails: 55)    | 0.898 / **$12.11** | 0.984 / $5.37 |

("fails" counts payloads that end on `assistant` over the 5 sessions; `off` only serves as
the append-only baseline.)

- **Each env update costs about one uncached re-read of the conversation:**
  extra ≈ *conversation tokens* × (input − cached) = C × $3.80/1M on Opus 5.5.
  - That is **$0.38 at a 100k-token history** and $0.67 at this simulation's mean.
  - If Copilot bills the miss at the cache-write rate ($5.00), it is about $0.48 per 100k.
  - 10 updates in a session more than double the input bill (+126%, $5.37 → $12.11).
- **`inplace` keeps the history append-only.** It matches `off` within 0.1% in every
  setting. Like `strip`, it never produces a payload that ends on `assistant` and never
  leaves a tool call unanswered (0 problems across all sessions).
- **When no update happens, the fold costs nothing.** The first env message is folded
  identically on every request, so the prefix is stable.

### Caveats

- The cache model is idealized. Copilot's real prefix and caching rules for Claude are
  unknown, and so is whether a miss is billed at the input rate or the cache-write rate.
- The session sizes are synthetic. The per-event formula above transfers better than the
  dollar totals.
- How often Claude Code emits env updates in normal use is unmeasured: `cd` in Bash,
  worktree changes, new `--continue` processes. Other mid-session system messages may
  exist and would add to it.
- `inplace` shows the model the env text as a user turn instead of system text. That is a
  fidelity trade-off, not measured here.
  - The faithful fix would be in copilot-api: translate `role:"system"` to an OpenAI
    `system` message in place (ericc-ch/copilot-api#272). The shim cannot do that on the
    Anthropic path.
- `hint` mode can also break the prefix, because it appends to a user turn that the next
  request carries without the hint. The capture had no prefill, so this was not measured.

## Sources

- GitHub Changelog, "Updates to GitHub Copilot billing and plans" (2026-06-01):
  https://github.blog/changelog/2026-06-01-updates-to-github-copilot-billing-and-plans/
- Models and pricing for GitHub Copilot (Claude rates per 1M tokens: input, cached
  input, cache write, output):
  https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing
- Usage-based billing for individuals:
  https://docs.github.com/en/copilot/concepts/billing/usage-based-billing-for-individuals
- Request-based billing (legacy), now only for annual Pro/Pro+ plans; one premium request
  per user prompt, tool calls free:
  https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/copilot-requests
- Community FAQ on the switch: https://github.com/orgs/community/discussions/197089
- Other clients' `X-Initiator` heuristics (third-party, unverified):
  https://github.com/DuongJs/copilotapi, https://github.com/anomalyco/opencode/pull/8721
- copilot-api 0.7.0 `create-chat-completions.ts` (`X-Initiator`) and
  `non-stream-translation.ts` (drops `cache_control`; relays `cached_tokens`).
- Claude Code 2.1.321 request bodies captured locally (`capture_shape.json`).

## What the planner should do next

1. **Reword the earlier finding in HANDOVER and the live-run checklist.** The
   copilot-translate note's `X-Initiator`/premium-request concern applies only to legacy
   annual Pro/Pro+ plans. On usage-based plans, watch AI-credit spend and the
   `cache_read_input_tokens` that copilot-api relays.
2. **Shim change, one small PR, to decide:** stop folding *later* `role:"system"`
   messages into `system`, so that history stays append-only.
   - Option A, as prototyped: convert every system message to a user message in place.
   - Option B: keep folding only a system message that is trailing on the first request
     or sits at index 1, and convert later ones in place.
   - Add a regression test built from `capture_shape.json`: the rendered prefix of
     request *n* must be a prefix of request *n+1*.
   - Also update the `rewrite.py` docstring and `test_system_role.py`, which describe the
     env block as trailing; that is true only on the first request.
3. **Live-run checklist addition:** run `cd` once mid-session, and compare
   `cache_read_input_tokens` on the next response in `strip` mode against the
   prototype's. This is the only way to learn Copilot's real cache behaviour.
4. **Optional upstream:** propose in copilot-api#272 that `role:"system"` be translated
   to an OpenAI `system` message in place. That fixes the prefill problem and cache
   stability for every client, not just the shim.
