# 2026-11-10: What do copilot-api's count_tokens answers do to Claude Code's size limits?

## Question

The milestone is a working Claude Code session on Copilot through the shim. Earlier lab notes
covered the main `/v1/messages` route only. Claude Code 2.1.321 also calls
`POST /v1/messages/count_tokens`. The shim forwards that call to copilot-api, which answers it
locally with a GPT tokenizer: `src/routes/messages/count-tokens-handler.ts` and
`src/lib/tokenizer.ts` in v0.7.0.

> Which Claude Code decisions depend on count_tokens? How do copilot-api's answers change them
> compared with an upstream that has no count route? Should the shim do anything?

## Method

1. **Static read of the 2.1.321 bundle**, extracted from the native binary. The counting
   function (`VXe`) is called from 4 places:
   - **Read size cap.** Claude Code first estimates a file locally (bytes/4). Above
     25000/4 = 6250 estimated tokens it asks count_tokens. Over 25000 tokens
     (`CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS`) the Read is cut to a page.
   - **MCP output limit** (`MAX_MCP_OUTPUT_TOKENS`, default 25000). The local estimate counts
     text plus 1600 per image. Up to 12500 the result is kept without a count. Text over 100000
     is truncated without a count. In between, count_tokens decides, and over 25000 the result
     gets truncated.
   - `/context` and tool-definition sizes: display only.

   When the count call fails (a null result), Claude Code uses its local estimate.
   copilot-api's handler has three properties that matter:
   - it looks up `body.model` exactly in Copilot's model list, and returns `{"input_tokens": 1}`
     on a miss or on any exception;
   - it translates the body to OpenAI form and counts o200k tokens, so image blocks become
     `data:` URLs and the whole base64 string is counted, plus 85;
   - it multiplies Claude counts by 1.15.
2. **Real handler, real client.** `count_server.ts` mounts copilot-api 0.7.0's own
   `messageRoutes` (handler, translation and tokenizer unchanged, at `0ea08fe`) with a fixed
   model list (`claude-opus-5.5`, `claude-sonnet-5.5`, `claude-haiku-4.5`, tokenizer
   `o200k_base`). `fake_upstream.py` answers `/v1/messages` with scripted tool calls. For
   count_tokens it uses one of three backends:
   - `copilot`: copilot-api's answer;
   - `miss`: the same handler, with the requested model absent from its list;
   - `none`: a 404, which makes Claude Code estimate locally.

   `run.py` drives real Claude Code 2.1.321 headless (`-p`, throwaway HOME, no credentials, no
   network) over 96 cases:
   - Read of one file: 3 corpora (TypeScript `lib.dom.d.ts`, `bun.lock` JSON, markdown READMEs)
     × 8 sizes from 20k to 250k chars × 3 backends;
   - an MCP tool `dump` returning text plus 0–9 noise-PNG "screenshots": 8 shapes × 3 backends.

   `reads.py` and `show.py` summarise and inspect the tool_results. A separate probe recorded
   which model ID Claude Code puts on count requests for 6 `ANTHROPIC_MODEL` values.
3. **Fix prototype.** `proto_fix.py` replaces base64 images in a count body with
   `[image]`, then adds Anthropic's documented image cost (w·h/750, capped at 1600). It
   compares copilot-api's answer before and after against the 25000 limit.

## Results

### 1. Read pages: copilot-api's count sets the page size; a model miss removes the cap

Columns are characters and lines of `big.txt` delivered by a single Read. Each line pair of
cells is copilot / none / miss.

| file | copilot-api count | copilot | none (local estimate) | miss (count = 1) |
|---|---|---|---|---|
| code 110k | 27,755 | 80k / 2649 l | 82k / 2673 l | **126k / all 3460 l** |
| code 250k | 65,098 | 60k / 2120 l | 63k / 2208 l | **281k / all 6497 l** |
| json 50k | 28,011 | 38k / 320 l | 52k / all | 52k / all |
| json 90k | 51,098 | 37k / 318 l | 93k / all | 93k / all |
| json 250k | 142,008 | 38k / 320 l | 88k / 728 l | **260k / all 2143 l** |
| md 90k | 26,999 | 76k / 1874 l | 101k / all | 101k / all |
| md 250k | 76,422 | 77k / 1940 l | 100k / 2372 l | **284k / all 6978 l** |

- Below about 6250 estimated tokens (the 20k-char files), no count request is sent. All 9
  cases are identical across backends.
- The cut is not silent. Claude Code appends a trailing role:system notice to the turn:
  "showing lines 1-314 of 579 total (39156 tokens, cap 25000). Call Read with offset=315…".
  The model pages through the file and loses nothing.
- copilot-api counts tokens per character as follows (90k-char files, ×1.15 included):
  - code: 0.25 tokens/char (3.96 chars per token);
  - markdown: 0.30 (3.33 chars per token);
  - JSON with base64 hashes: 0.57 (1.76 chars per token).

  Claude Code's local fallback assumes 4 chars per token for every kind. So copilot-api gives
  pages of the same size for code, 24 % smaller for markdown, and 57 % smaller for this JSON
  (320 vs 728 lines). By copilot-api's own count, the 404 fallback's JSON page is about
  50k tokens, twice the cap.
- I can't say which of the two is closer to Claude's real tokenizer: I made no real Anthropic
  or Copilot count call.
- **With a model miss, the cap is gone.** Every file arrives whole in one tool_result, at 2.6×
  to 5.7× the 25k cap by copilot-api's own count. This matters behind Copilot's prompt cap:
  see compact-cap.

### 2. When the model ID misses

Model ID that Claude Code puts on the count request, per `ANTHROPIC_MODEL` setting:

| `ANTHROPIC_MODEL` | model on count request | copilot-api answer |
|---|---|---|
| `claude-opus-5.5` | `claude-opus-5.5` | real count |
| `claude-opus-5.5[1m]` | `claude-opus-5.5` | real count |
| `opus` | `claude-opus-5-5` | **1** |
| `sonnet` | `claude-sonnet-5-5` | **1** |
| `claude-opus-5-5` | unchanged | **1** |
| `claude-sonnet-4-5-20250929` | unchanged | **1** |

- copilot-api's main route rewrites `claude-sonnet-4-*` and `claude-opus-4-*` IDs
  (`handler.ts:49`), but the count route does not. So Claude-4-era dated IDs can work for chat
  while every count returns 1.
- The shim's `--map` applies to the count_tokens path as well (`server.py:28-30`). A mapped ID
  therefore fixes counting too.

### 3. MCP images: a false "OUTPUT TRUNCATED" banner

| MCP result | count calls | copilot-api count | prototype fix | copilot outcome | none / miss outcome |
|---|---|---|---|---|---|
| 40k chars + 1 × 300 px | 0 | not asked | n/a | kept | kept |
| 55k chars + 1 × 300 px | 2 | 299,683 | 16,535 | **banner** | kept |
| 55k chars + 1 × 120 px (43 KB PNG) | 2 | 62,084 | 16,434 | **banner** | kept |
| 40k chars + 3 × 200 px | 2 | 390,203 | 11,889 | **banner** | kept |
| 20k chars + 8 × 200 px | 2 | 1,015,495 | 6,419 | **banner** | kept |
| 2k chars + 9 × 200 px | 2 | 1,136,367 | 1,118 | **banner** | kept |

- The banner fires in all 5 cases where a count was asked. In each, Claude Code still keeps
  all the text and every image: its truncation budget is 100,000 characters, and an image
  costs only 6,400 of them. The visible change is the appended text:
  "[OUTPUT TRUNCATED - exceeded 25000 token limit] … inform the user that you are working
  with truncated output and results may be incomplete".
  - The model is told that a complete result is incomplete.
  - Claude Code also sends the count twice in this path (2 requests instead of 1).
- The cause is that each image is counted as its base64 text: roughly 1 token per base64
  character, so about 280k tokens for a 270 KB PNG.
- With images priced at w·h/750 (prototype), 6/6 image cases fall below 25000. The text-only
  case is unchanged at 26,999.
- Text-only MCP results:
  - 60k chars: identical in all backends (saved to a file with a 2.4k-char preview).
  - 90k chars: copilot-api's 26,999 turns the preview into "Error: result … exceeds maximum
    allowed tokens. Output has been saved to …". The full output is saved to a file in every
    backend; only the wording differs.

## What the planner should do next

1. **Shim: fix image counting on the count_tokens path.** Before forwarding, replace base64
   `image` blocks with a text placeholder and add w·h/750 (capped at 1600) to the answer.
   Treat `document` blocks the same way. This takes about 40 lines of stdlib code
   (`proto_fix.py`) and removes 6/6 false banners without changing text counts. Test it with
   `tests/fake_upstream.py` returning a fixed count.
2. **Shim: never relay `{"input_tokens": 1}`.** For a non-empty body, that answer means
   copilot-api's model lookup missed or its handler threw. Answer 404 instead, so Claude Code
   falls back to its local estimate. Measured: the `none` backend keeps the 25k page cap in
   every oversized case (63k–101k-char pages), where `miss` passes up to 284k chars. Log a
   warning that names the model and suggests `--map`.
3. **Docs (`SETUP.md`):** say that `ANTHROPIC_MODEL=opus`/`sonnet` aliases and dated IDs
   break copilot-api's token counting even where chat works, and that `--map` fixes both.
4. **Open, needs the owner's login or key:** calibrate o200k ×1.15 against real Claude counts
   for code, markdown and JSON. That decides whether copilot-api's or the local estimate's page
   size is closer to right. Low priority: pages are announced and recoverable either way.
5. Cross-link: every cut Read adds a trailing role:system notice, which the shim folds into
   `system`. That is the cache-breaking path in cache-prefix. copilot-api's denser JSON count
   means more pages, and so more such notices.

## Files

- `lab/count-tokens/count_server.ts`: copilot-api's own count route, standalone (Bun).
- `lab/count-tokens/fake_upstream.py`: fake Messages endpoint with a `COUNT=` backend switch.
- `lab/count-tokens/mcp_server.py`: stdio MCP server with the `dump` tool (text + PNGs).
- `lab/count-tokens/run.py`: the 96-case matrix. `results.json` holds the raw outcomes and
  count answers.
- `lab/count-tokens/reads.py` → `reads.json`: per-Read lines and characters delivered.
  `show.py`: tool_result inspector.
- `lab/count-tokens/proto_fix.py` → `proto_fix.json`: image-cost rewrite prototype.
  `smoke.py`: handler smoke test.

Reproduce from the repo root:

1. `cd .agent-work/copilot-api && bun install`
2. `python3 lab/count-tokens/run.py $PWD/.agent-work/copilot-api <claude-linux-x64 binary>`

## Sources

- copilot-api v0.7.0 (`0ea08fe`): `src/routes/messages/count-tokens-handler.ts`,
  `src/lib/tokenizer.ts`, `src/routes/messages/handler.ts:49`.
  https://github.com/ericc-ch/copilot-api
- Claude Code 2.1.321 native bundle: counting function `VXe`; Read cap (`oHs=25000`,
  `iHs=128`); MCP limits (`k=0.5, y=4, M=1600, B=25000`).
- Anthropic token counting API: https://docs.claude.com/en/docs/build-with-claude/token-counting
- Anthropic image cost (tokens ≈ width·height/750):
  https://docs.claude.com/en/docs/build-with-claude/vision
