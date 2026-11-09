# 2026-11-09: Does the shim's fix survive copilot-api's translation?

## Question

The shim makes sure the Anthropic body it forwards ends on a user turn. Copilot never sees
that body. copilot-api first rewrites it into an OpenAI Chat Completions payload
(`translateToOpenAI`), and Copilot sees only that payload. The milestone is to get Claude
Code working through the shim against real Copilot. Its last open step needs a Copilot
login. One part can be checked without a login:

> For Claude-Code-like requests, does the OpenAI payload that copilot-api builds from the
> shim's output still end on a non-assistant turn? What else does the translation do to
> that output?

Earlier lab notes tested `rewrite()` alone (rewrite-fuzz) and the shim's relay (sse-relay).
Neither one ran the copilot-api half of the chain.

## Method

- **Upstream code under test:** `ericc-ch/copilot-api` at `0ea08feb` (2025-10-05), v0.7.0.
  `npm view copilot-api` also reports 0.7.0, last modified 2025-10-05, so this is exactly
  what `npx copilot-api@latest` runs today. It was cloned into `.agent-work/`, which git
  ignores.
- **Harness:** `lab/copilot-translate/translate.ts` imports copilot-api's own
  `src/routes/messages/non-stream-translation.ts` with bun 1.3.3 and calls
  `translateToOpenAI` on each body. It installs nothing and makes no network or Copilot
  calls. `lab/copilot-translate/run.py` builds the corpus, runs `copilot_shim.rewrite` in
  modes `off`/`strip`/`hint` (shim code at `main` @ 960daf7), translates each result and
  checks the OpenAI payload.
- **Corpus (2012 bodies × 3 modes):**
  - the 5 `tests/fixtures/prefill_requests.json` messages fixtures;
  - 7 hand-written edge cases;
  - 2000 seeded random Claude-Code-like conversations. These include text, image,
    `document` and `thinking` blocks, parallel `tool_use` calls answered by `tool_result`s
    (some with images or `is_error`), a `<system-reminder>` text block after tool
    results, `role:"system"` messages at random positions, a cached top-level `system`,
    50% trailing prefill, and models `claude-opus-5.5`, `claude-sonnet-5.5`, dated
    `claude-{opus-4-8,sonnet-4-6}-2026…` and `claude-haiku-4.5`.
- **Checks on the OpenAI payload:**
  - the last non-system message is `assistant` (which Copilot answers with the prefill
    400);
  - each `tool_calls` is answered by the `tool` messages directly after it;
  - no `user` or `tool` message has empty content;
  - all Anthropic text survives;
  - images stay out of `tool` messages;
  - `thinking` doesn't come back as assistant text;
  - the `X-Initiator` value copilot-api would send;
  - model renames.
- **Run:** `python3 lab/copilot-translate/run.py .agent-work/copilot-api 2000` takes 1.9 s.
  Raw numbers are in `lab/copilot-translate/results.json`.

## Results

The counts below come from one seeded generator. Use them to tell which defect classes
exist and how `off`, `strip` and `hint` compare. They are not real-traffic rates.

| check (bodies out of 2012) | off | strip | hint |
|---|---:|---:|---:|
| OpenAI payload ends on `assistant`, so Copilot returns the prefill 400 | **881** | **1** | **0** |
| `tool_call` not followed by its `tool` result | **188** | **0** | **0** |
| `X-Initiator: user` / `agent` | 103 / 1909 | 332 / 1680 | 332 / 1680 |
| `user` message with empty content | 200 | 200 | 179 |
| text lost (only `document` blocks) | 794 | 794 | 794 |
| image inside a `tool` message | 949 | 949 | 949 |
| `thinking` replayed as visible assistant text | 1140 | 1140 | 1140 |
| translator exceptions | 0 | 0 | 0 |

The named cases (fixtures and edge cases) in `results.json` show:

1. **The fix holds through translation.** All 5 fixtures end on `assistant` in `off` mode.
   In `strip` and `hint` they end on `user`, or on `tool` (`after_tool_result`: roles
   `user, assistant, tool`).
   - The 2.1.x first turn (`hello` plus a trailing `role:"system"` env block) ends on
     `assistant` in `off` mode, as ericc-ch/copilot-api#272 describes. It ends on `user`
     in `strip`.
   - Open assumption: Copilot treats a trailing `tool` message as an ordinary agent-loop
     turn and not as prefill. Only a real login can confirm this.
2. **The one `strip` failure** is a user turn whose `content` is `[]`, followed by a
   prefill:
   - The shim removes the prefill. The Anthropic body then ends on `user`, which looks
     right.
   - copilot-api's `handleUserMessage` turns an empty block list into **zero** OpenAI
     messages. The payload therefore ends on the assistant turn before it, and Copilot
     would return the prefill 400 again.
   - `hint` avoids this by accident, because it appends text to that same user turn.
   - The Anthropic API rejects empty content, so Claude Code probably never sends this.
     The defect is low-likelihood but real: the shim checks the last *Anthropic* role,
     and copilot-api can still remove the turn that check relied on.
3. **Folding `system` messages also fixes tool-call ordering.** In `off` mode, a
   `role:"system"` message between a `tool_use` and its `tool_result` becomes an
   `assistant` message (#272 again). That separates the tool call from its result in 188
   bodies, which an OpenAI-style backend normally rejects. `strip` and `hint` bring this
   to 0.
4. **The shim changes billing headers.**
   - copilot-api sends `X-Initiator: agent` if *any* message is `assistant` or `tool`.
     According to copilot-api's own comment, that keeps a call from counting as a
     user-initiated (premium) request.
   - In `off` mode, the misrouted `role:"system"` message and the prefill itself make
     copilot-api label 229 of 2012 bodies `agent`. These are first-turn and helper calls
     with no real assistant turn.
   - After the shim they are `user`. That label is correct, but it may count toward
     premium requests. Claude Code's prefilled helper calls would then each cost a premium
     request, where before they failed with a 400.
   - This is unverified. Watch the premium-request counter during the live run.
5. **Defects in copilot-api 0.7.0 that the shim neither causes nor fixes:**
   - `document` blocks (PDF/text attachments) are dropped silently. A turn that holds only
     a document becomes a `user` message with content `""`.
   - Images in `tool_result` end up inside OpenAI `tool` messages. Many OpenAI-shaped
     backends reject that, and whether Copilot accepts it is unknown.
   - `thinking` text from earlier turns comes back joined into the visible assistant
     content, after the text. That pollutes the history and wastes context.
6. **Model IDs.** copilot-api renames `claude-opus-4-*` to `claude-opus-4` and
   `claude-sonnet-4-*` to `claude-sonnet-4`. It leaves `claude-*-5-*` and dated
   `claude-haiku-4-5-…` IDs unchanged.
   - Dated 4.x IDs therefore land on IDs that are absent from the HANDOVER's model list.
   - A dated `claude-haiku-4-5-20251001` request has its prefill stripped, because the
     `prefill_ok` glob is `claude-haiku-4.5`. It is also forwarded unchanged.
   - The documented `--map claude-haiku-4-5-20251001=claude-haiku-4.5` fixes both
     problems, because the map runs before the prefill decision. Use `--map` for any
     dated ID that the shim log shows.

## Sources

- copilot-api source: https://github.com/ericc-ch/copilot-api, specifically
  `src/routes/messages/non-stream-translation.ts` (`translateModelName`,
  `handleUserMessage`, `handleAssistantMessage`, `mapContent`) and
  `src/services/copilot/create-chat-completions.ts` (`X-Initiator`).
- npm registry: `copilot-api` 0.7.0, modified 2025-10-05.
- ericc-ch/copilot-api#272 (non-user roles translated to `assistant`), cited in
  `copilot_shim/rewrite.py`.

## What the planner should do next

1. **Small shim hardening (one PR):** in `rewrite`, treat a trailing `user` message with
   empty content (`[]` or `""`) as missing when deciding whether the body ends on
   assistant. Either drop it together with the prefill, or put `hint`-style text into it.
   Add `edge:empty_user_list_then_prefill` as a regression test. The rewrite-fuzz
   robustness fixes (R1–R4) could ship in the same PR.
2. **Add to the live-run checklist (HANDOVER "Still to do"):**
   - confirm that a body ending on `tool` is accepted;
   - note whether the premium-request counter goes up for shim-rewritten helper calls
     (`X-Initiator: user`);
   - record every dated model ID in the shim log and add a `--map` for each.
3. **Optional, needs an owner decision:** the shim could also cover copilot-api's
   translation gaps, by dropping `thinking` blocks from history or turning `document`
   text into a text block. These are fidelity fixes beyond the prefill milestone, so
   raise them as a question and don't build them yet.
