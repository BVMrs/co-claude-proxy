# 2026-11-10: Do Claude Code's thinking and effort settings reach Copilot?

## Question

The milestone is getting Claude Code working through the shim against real Copilot. Earlier lab
notes looked at what copilot-api's translation does to **messages**: prefill, tool-call order,
thinking text in the history, and tool_reference. None of them looked at the request's
**top-level controls**. This note asks:

> Real Claude Code bodies carry request-level controls: `thinking`, `output_config.effort`,
> `output_config.format`, `context_management`, `max_tokens` and `metadata`. Which of them
> reach Copilot through shim → copilot-api 0.7.0? If any are lost, what route would carry them?

## Method

1. **Measurement (offline):** `lab/thinking-fields/survey.py` reads the 97 real Claude Code
   2.1.321 `/v1/messages` bodies that earlier lab runs captured. These come from 9 cc runs, 72
   prefill-source runs and 16 tool-search runs, all in `.agent-work/`, which git ignores. They
   are not committed because `metadata.user_id` holds a device_id. The script runs each body
   through `copilot_shim.rewrite` (default strip policy, Anthropic shape). It then runs the
   result through copilot-api's own `translateToOpenAI`, using
   `lab/copilot-translate/translate.ts` under bun 1.3.3 with copilot-api at `0ea08fe`, which is
   v0.7.0 and still `npm latest` today. For each field it checks whether the value reaches the
   OpenAI payload that Copilot receives. It makes no network or Copilot calls.
   - Run: `python3 lab/thinking-fields/survey.py .agent-work/copilot-api .agent-work/cc/run2.jsonl .agent-work/prefill-src/*.jsonl .agent-work/tool-search/t*.jsonl`
   - Output: `lab/thinking-fields/results.json`.
2. **Prior art (source reading):** I read how two Copilot clients send thinking and effort to
   Copilot for Claude models:
   - microsoft/vscode-copilot-chat @ `5863f5a` (2026-05-20);
   - zed-industries/zed `crates/copilot_chat` @ `0929d56` (2026-11-10).

   I also read the open copilot-api PRs that change this path: #280 @ `6d124a7` and #246 @
   `74dec5c`.
3. **Routing check:** I applied PR #280's routing predicate (`shouldUseNativeMessages`, mirrored
   in Python from `native-handler.ts`) to the same 97 bodies, both raw and after the shim.

## Results

### 1. What Claude Code sends, and what reaches Copilot (97 bodies)

| field | sent in | values seen | in the OpenAI payload Copilot sees |
|---|---:|---|---:|
| `thinking` | 85 | adaptive/display=updates 54, adaptive/display=omitted 25, disabled 6 (Haiku title helper) | **0** |
| `output_config.effort` | 79 | `medium` 79 (CC 2.1.321 default) | **0** |
| `output_config.format` (json_schema `{title}`) | 18 | session-title helper | **0** (no `response_format` either) |
| `context_management` | 79 | `clear_thinking_20251015, keep: all` | **0** |
| `max_tokens` | 97 | 128000 (Opus loop) 91, 32000 (Haiku) 6 | 97, verbatim |
| `metadata.user_id` | 97 | JSON with device_id + session_id | 97, as OpenAI `user` (150 chars, includes device_id 97/97) |
| `temperature` | 6 | 1 | 6 |

The OpenAI payload carries none of `reasoning_effort`, `thinking_budget`, `response_format`,
`thinking`, `output_config` or `context_management` (0/97 for each). copilot-api 0.7.0's
`translateToOpenAI` builds its output from a fixed list of fields (`non-stream-translation.ts:29-44`),
so the shim cannot add a field in front of it.

**What this means:**

- Every Claude Code turn runs on Copilot **without extended thinking**, and the user's effort
  setting is ignored. Copilot's Chat Completions path turns on thinking for Claude only when it
  gets an explicit `thinking_budget`. VS Code declares that field as
  `/** ChatCompletions API for Anthropic models */ thinking_budget?: number`
  (`networking.ts:123-124`), and copilot-api never sends it.
- This is consistent with the claim in copilot-api PR #280 (Copilot Business, 2026-10-27). It
  reports Claude Code `effort` low…max making no difference on the translation path. Directly
  on `/v1/messages` it measured 12, 79 and 241 thinking tokens for low, high and max. I did not
  reproduce that here, because it needs a login.
- Even if thinking were turned on, copilot-api 0.7.0 couldn't carry it back.
  `stream-translation.ts` has no `reasoning_text` or `reasoning_opaque` handling
  (`non-stream-translation.ts:305`: "Copilot doesn't generate thinking blocks"). It also replays
  history thinking as visible text (copilot-translate note). Patching only the request side
  would therefore break multi-turn tool use with thinking.
- The title helper loses its JSON schema. Its system prompt still says "Return JSON with a
  single "title" field", so it most likely still works, but nothing enforces the format.
- Privacy and limits side note: the device_id travels to GitHub inside OpenAI `user`.
  `max_tokens: 128000` is forwarded unclamped. Zed's fixture shows Copilot listing
  `max_output_tokens: 16384` for an older Claude. Whether Copilot clamps or rejects a larger
  value is unknown here.

### 2. Prior art: Copilot has a native Anthropic Messages endpoint

- **VS Code Copilot Chat:**
  - Sends Claude models to `{capi}/v1/messages` when the model's `/models` entry lists
    `"/v1/messages"` in `supported_endpoints`. The setting `chat.anthropic.useMessagesApi`
    defaults to `true` (`configurationService.ts:881`, `chatEndpoint.ts:248-251`).
  - On that path it sends `thinking: {type: 'adaptive'}` or `{type:'enabled', budget_tokens}`,
    `output_config: {effort}`, `context_management`, `cache_control` on tools and system, and
    tool search with `defer_loading` and `tool_reference` (`messagesApi.ts:144-236`).
  - Betas in use: `interleaved-thinking-2025-05-14`, `context-management-2025-06-27`,
    `advanced-tool-use-2025-11-20` (`chatEndpoint.ts:196-210`).
  - It also appends a synthetic `'Please continue.'` user turn when the body ends on assistant
    (`messagesApi.ts:198-224`). This is the same fix as the shim's `hint`/`strip` modes, made
    for the same 400.
- **Zed:** `copilot_chat.rs:62` `messages_url()` = `{api_endpoint}/v1/messages`. A test fixture
  (`:1732-1766`) shows a Claude model whose only listed endpoint is `"/v1/messages"`.
  `capabilities.supports` carries `adaptive_thinking`, `max/min_thinking_budget` and
  `reasoning_effort`.
- **copilot-api upstream:** Nothing is merged. Open PRs:
  - **#280** (2026-10-27): forwards `claude*` bodies unchanged to `/v1/messages`, plus
    `anthropic-version`/`anthropic-beta` and `X-Initiator`. It retries once when Copilot rejects
    a `thinking.type` and names a replacement. It keeps prefill requests on the translation
    path. The author tested it against real Copilot (above). The author also reports billing:
    46.94 vs 37.53 AI credits for a ≈94k prompt on chat/completions vs messages.
  - **#246** (2026-04): a sanitizing passthrough that **strips** `context_management`,
    `defer_loading` and `tool_reference`. No real-Copilot results. Its stripping contradicts
    what VS Code sends today.
  - **#238**: thinking translation on Chat Completions.
  - **#269**: route each model by `supported_endpoints`.

### 3. How the shim fits with a native path (PR #280 routing over the 97 bodies)

| | raw Claude Code body | after shim (strip) |
|---|---:|---:|
| routed native (`claude*`, last role ≠ assistant) | 97 | 97 |
| … still containing a `role:"system"` message | **79** | **0** |
| … ending on `role:"system"` | 18 | 0 |
| bodies with `cache_control` markers (would now be honoured natively) | – | 79 (237 markers) |

- PR #280's predicate checks only for a trailing `assistant`. So all 18 trailing-`role:system`
  bodies go native. Those are the source of every prefill 400 in the prefill-source note. 79/97
  bodies would reach Copilot's native endpoint with a `role:"system"` message, which the
  Anthropic Messages API does not define as a role. I could not observe what Copilot does with
  it.
- The shim's fold removes this in 97/97 bodies. **The shim stays useful on the native path.**
  Its prefill strip also moves the true-prefill case (0 in these captures) onto the native path.
- One trade-off grows on the native path. The cache-prefix note found that folding `role:system`
  into `system` re-reads the history uncached on every env update. Natively, `cache_control` is
  honoured, so cache hits are real money and the fold's cost matters more. The in-place
  system→user conversion that note recommended is the better fold for this route.

## What the planner should do next

1. **Owner question (don't decide here):** move Claude traffic to Copilot's native
   `/v1/messages`? Two ways:
   - (a) run copilot-api from PR #280's branch behind the shim. Smallest change, and it already
     has real-Copilot evidence.
   - (b) teach the shim to forward natively itself. This needs the shim to do the Copilot token
     exchange, which is a much larger scope.

   Either route fixes thinking and effort. It would probably also fix the copilot-translate
   defects, tool_reference loss (tool-search note), and maybe the compact-cap error format, since
   the native errors should be Anthropic-shaped. That last point is unverified.
2. **Login test checklist** (add to HANDOVER's "still to do"):
   - `GET {capi}/models` and record `supported_endpoints` and `capabilities.supports` for each
     Claude ID. copilot-api's `/v1/models` strips both (`routes/models/route.ts:16-24`), so
     `probe --list` can't show them. A probe option that reads them needs the Copilot token,
     e.g. `copilot-api start --show-token`.
   - With PR #280 behind the shim, send one turn with `effort` low and one with high, and
     compare `usage` thinking tokens.
   - Send one raw (unshimmed) body with a `role:"system"` message to see whether native rejects
     it.
   - Check `max_tokens: 128000` against Copilot's `max_output_tokens`.
3. **Shim change, independent of route:** switch the system fold to in-place system→user
   (cache-prefix note). It is cheap now and matters more natively.
4. **Low priority:** have the shim drop or hash `metadata.user_id` before forwarding, so the
   device_id doesn't reach GitHub as OpenAI `user`. Check with the owner first, since it changes
   what Copilot sees.

## Sources

- copilot-api `src/routes/messages/non-stream-translation.ts:29-44, 146-211, 305`;
  `src/routes/models/route.ts`: https://github.com/ericc-ch/copilot-api (v0.7.0, `0ea08fe`)
- copilot-api PR #280: https://github.com/ericc-ch/copilot-api/pull/280 ; PR #246:
  https://github.com/ericc-ch/copilot-api/pull/246 ; PR #238:
  https://github.com/ericc-ch/copilot-api/pull/238 ; PR list:
  https://github.com/ericc-ch/copilot-api/pulls?q=is%3Apr+thinking
- vscode-copilot-chat `src/platform/endpoint/node/messagesApi.ts`, `chatEndpoint.ts`,
  `src/platform/networking/common/networking.ts`,
  `src/platform/configuration/common/configurationService.ts`:
  https://github.com/microsoft/vscode-copilot-chat
- Zed `crates/copilot_chat/src/copilot_chat.rs`: https://github.com/zed-industries/zed
- byokey-proxy Copilot messages handler (third-party; same endpoint and headers):
  https://docs.rs/crate/byokey-proxy/1.2.0/source/src/handler/messages.rs
- VS Code language-model docs (thinking-effort settings):
  https://code.visualstudio.com/docs/agent-customization/language-models
- Earlier lab notes: copilot-translate, prefill-source, cache-prefix, tool-search, compact-cap.

**Limits:** No Copilot login was used. Every statement about how Copilot itself behaves comes
from client source code or PR #280's author, not from my own observation. The captured bodies
come from Claude Code talking to fake upstreams, so the history never holds thinking blocks
(0/97). The routing check mirrors PR #280's predicate in Python; I did not run its server.
