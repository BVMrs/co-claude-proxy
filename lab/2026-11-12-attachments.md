# Images and PDFs from Claude Code through shim + copilot-api 0.7.0

Date: 2026-11-12 · Track: d (end-to-end test against main) + prototype · Code: `lab/attachments/`

## Question

When Claude Code 2.1.321 reads an image, a screenshot, a notebook with plots or a PDF, what does
Copilot actually receive after the shim (main) and copilot-api 0.7.0's `translateToOpenAI`? Can the
shim make that payload valid without a Copilot login?

## Method

1. `make_files.py` writes `small.png` (64×48, 354 B), `screen.png` (1600×1000 noise, 4.8 MB),
   `spec.pdf` (2 pages, 865 B) and `plot.ipynb` (a cell with a PNG output).
2. `run_case.sh` runs headless Claude Code 2.1.321 → shim (main, default flags) → fake Anthropic
   endpoint (`capture_server.py`, copied from lab/web-search). A `PLAN:` prompt makes the fake model
   call `Read` on each file in turn. A second run uses `@small.png` / `@spec.pdf` mentions instead.
   The fake endpoint records each body *after* the shim, which is what copilot-api receives.
3. Two more bodies: a real MCP screenshot result captured by lab/count-tokens (3 PNGs plus text in
   one tool_result), and a synthetic parallel-call turn (two image tool_results in one user message).
4. `check.py` runs each body through copilot-api 0.7.0's own `translateToOpenAI` (bun, commit
   0ea08feb), once as captured and once after `proto.hoist_attachments`. It counts `image_url`
   parts in non-`user` messages, PDF bytes that never reach the payload, and whether tool messages
   still directly follow their `tool_calls`.

No network, no Copilot login. **What real Copilot does with the main-branch payload is unverified.**

## Results

What Claude Code sends (bodies after the shim; the shim leaves attachments alone):

| Source | Anthropic block(s) |
|---|---|
| Read small.png | `tool_result[image/png base64 472 chars]` |
| Read screen.png (4.8 MB) | `tool_result[image/jpeg 675 464 chars]` (CC downscales and re-encodes it) |
| Read spec.pdf | `tool_result[text "PDF file read: … (865 bytes)", document application/pdf 1156 chars]` |
| Read plot.ipynb | `tool_result[text (cell source), image/png]` |
| `@small.png` mention | top-level `image` in the user turn |
| `@spec.pdf` mention | **text only**: "PDF file read: … (865 bytes)". CC 2.1.321 sends no document block at all, so this is a Claude Code behaviour, not a proxy one |
| MCP screenshot tool | `tool_result[text, image, text, image, text, image]` (3 × 160 kB PNG) |

What copilot-api 0.7.0 makes of it (`check.py`, `results.json`):

| Body | main: `image_url` in role `tool` | main: PDF content reaching Copilot | proto: `image_url` in role `tool` | proto: images delivered (in role `user`) | tool ordering |
|---|---|---|---|---|---|
| Read ×4 | 3 of 3 | 0 of 1156 chars, with no marker | 0 | 3 of 3 | ok / ok |
| @-mention | 0 of 1 | n/a | 0 | 1 of 1 | ok / ok |
| MCP screenshots | 3 of 3 | n/a | 0 | 3 of 3 | ok / ok |
| parallel 2 images | 2 of 2 | n/a | 0 | 2 of 2 | ok / ok |

Findings:

1. **Every image a tool returns ends up inside an OpenAI `role: "tool"` message** (8/8 tool images).
   `mapContent` copies `image` blocks into `image_url` parts no matter what the role is. OpenAI's Chat
   Completions API rejects this with 400 "Image URLs are only allowed for messages with role 'user'"
   (forum reports below). Images the user attaches directly (@-mention or paste) are fine (1/1 in
   `user`).
2. **This is sticky.** The image stays in history, so every later request in the session carries it.
   If Copilot behaves like OpenAI, one `Read foo.png` or one MCP screenshot breaks every remaining
   turn until `/clear` or a rewind. That is the same failure shape as lab/compact-cap. If Copilot
   instead drops the part silently, the model answers about an image it never saw. Which of the two
   happens is unknown. `enableVision` is set either way: copilot-api scans every message for
   `image_url`, so the `Copilot-Vision-Request` header goes out.
3. **PDFs are silently dropped.** `mapContent` handles only text, thinking and image, so the
   `document` block disappears. The model gets just "PDF file read: <path> (865 bytes)" and no sign
   that any content is missing. It will guess or claim it read the file.
4. **Prototype (`proto.py`, about 50 lines, stdlib only).** For each user message:
   - Move each image out of a tool_result into the same message, after the tool_results, and leave a
     text marker `[image N attached in the next user message]` in its place. copilot-api already
     emits tool messages first and the other blocks as one following `user` message, so images land
     in `role: user` and tool-call adjacency is kept.
   - Replace each `document` with an explicit note telling the model the PDF was dropped and how to
     extract its text with a tool.

   Results: 0 images in non-user roles in 4/4 bodies, 9/9 images still delivered, ordering intact
   4/4. The transform is deterministic per message, so earlier history stays byte-identical across
   turns, which keeps the prompt-cache prefix append-only (see lab/cache-prefix). The translated
   payload still ends on `user`, so it adds no prefill exposure.
5. The native Messages route (lab/thinking-fields, copilot-api PR #280) would pass tool_result
   images and documents through unchanged. Whether Copilot's native endpoint accepts them is also
   unverified.

## Sources

- copilot-api 0.7.0 `src/routes/messages/non-stream-translation.ts` (`handleUserMessage`, `mapContent`), `src/services/copilot/create-chat-completions.ts` (`enableVision`)
- OpenAI forum, images only in user role: https://community.openai.com/t/gpt-4o-regression-doesnt-support-images-in-system-and-function-messages/760809, https://community.openai.com/t/returning-image-as-result-of-function-call-to-gpt-4-turbo/714903, https://community.openai.com/t/allowing-images-in-non-user-messages/804176
- Same workaround (move the image into a following user message) for MCP tools on OpenAI-compatible APIs: https://mcphelper4aeqor2wou.s3.amazonaws.com/docs/AB498-computer-control-mcp/issue-11/v3-7hv9ap.html
- Claude Code tool_result image issues (media type, 5 MB): https://claudeissues.com/issue/34517-api-error-400-invalid-request-error-for-tool-result-image-media-type, https://claudeissues.com/issue/13381-bug-anthropic-api-error-image-exceeds-5-mb-maximum-in-tool-result
- I couldn't search copilot-api's issue tracker: `gh` has no auth here.

## What the planner should do next

1. On first Copilot login, probe once: one Read of a PNG through main, then one more turn. Record
   whether Copilot returns a 400 or answers. Use `--verbose`, or the shim log plus the reply. This
   decides how urgent item 2 is.
2. Either way, add the image hoist to the shim's rewrite as default-on, with a flag to turn it off.
   If Copilot rejects, it fixes broken sessions. If Copilot drops, it makes screenshots usable.
   Bring over the unit cases from `check.py`: Read ×4, MCP, parallel, @-mention as no-op.
3. Add the PDF note now. It is cheap and turns silent wrong answers into an actionable message.
   Real PDF support would need text extraction, which the stdlib can't do in general. Leave that to
   the native route decision.
4. Document in SETUP: "PDFs read by Claude Code do not reach Copilot; use `pdftotext` via Bash."
