# co-claude-bridge

Run [Claude Code](https://docs.anthropic.com/en/docs/claude-code) on a GitHub Copilot subscription.

co-claude-bridge is a local server. It speaks the Anthropic Messages API to Claude Code
and calls the GitHub Copilot API upstream. It signs in to GitHub itself, so nothing else
needs to be running.

> **Unofficial.** This uses reverse-engineered Copilot endpoints that GitHub doesn't
> support or document. Heavy automated use can get an account flagged. Check
> [GitHub's terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#github-copilot),
> and your employer's policy if your Copilot seat is work-provided, before using it.

## Quick start

You need Node 20 or later (you already have it if you installed Claude Code with npm) and
a GitHub account with Copilot.

```bash
npx co-claude-bridge start
```

On first run it prints a code and a URL. Open the URL, enter the code, and the token is
saved for next time. The bridge then loads the models your account has and prints a
block like this:

```bash
export ANTHROPIC_BASE_URL='http://127.0.0.1:4141'
export ANTHROPIC_AUTH_TOKEN='co-claude-bridge'
export ANTHROPIC_MODEL='claude-opus-5.5'
export ANTHROPIC_DEFAULT_OPUS_MODEL='claude-opus-5.5'
export ANTHROPIC_DEFAULT_SONNET_MODEL='claude-sonnet-5.5'
export ANTHROPIC_DEFAULT_HAIKU_MODEL='claude-haiku-4.5'
export ANTHROPIC_SMALL_FAST_MODEL='claude-haiku-4.5'
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1'
unset CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX ANTHROPIC_API_KEY
claude
```

Paste it into another terminal. Every model ID in it has been checked against your
account. For fish or PowerShell, use `npx co-claude-bridge env --shell fish` (or
`powershell`).

## What it fixes

Running Claude Code through a generic OpenAI-compatible Copilot proxy hits a few
specific failures. The bridge handles each one:

| Symptom | Cause | What the bridge does |
| --- | --- | --- |
| `400 This model does not support assistant message prefill` on a plain "hello" | Claude Code 2.1.x ends its main request with a `role: "system"` message (the environment block). Generic proxies turn it into an assistant turn. | Moves `system` messages into the system prompt. |
| The same 400 on helper calls or resumed sessions | Claude 4.6+ models reject a request that ends on an assistant turn (a prefill). Haiku 4.5 still accepts one. | For models that reject it, moves the prefill text into an instruction on the last user turn, then strips the echoed text from the reply. Claude Code gets the reply Anthropic would have sent. Empty trailing turns left by interrupted sessions are dropped. |
| `model_not_supported` | Claude Code sends IDs like `claude-sonnet-4-5-20250929` or `claude-opus-5-5[1m]`. Copilot uses `claude-sonnet-4.5`. | Checks every ID against the account's `/models` list, normalizes dashed, dated and `[1m]` forms, and falls back to the matching family slot. If none of that works, it returns an error listing the valid IDs. |
| Requests fail with 401 after an hour | The Copilot token expires. | Refreshes the token before it expires, and on a 401 refreshes it and retries once. |
| No idea which request failed | | `--verbose` prints one line per request (see below). |

If a model surprises it, the bridge learns as it goes. When an unexpected prefill 400
comes back, it marks that model, retries with the repair, and remembers the result
across restarts.

## Commands

| Command | What it does |
| --- | --- |
| `start` | Signs in if needed, then runs the bridge. This is the default. |
| `auth` | Signs in to GitHub again and saves the token. |
| `logout` | Deletes the saved token. |
| `models` | Lists your account's models, with context size, tool support, and whether each accepts prefill. |
| `env` | Prints the Claude Code environment block. |
| `doctor` | Checks sign-in, the model list, the model slots, and Bedrock/Vertex settings that would bypass the bridge. `--probe` also tests prefill on every Claude model. That costs about 2 premium requests per model, and the results are saved. |

## Flags

Every flag can also be set with a `COB_<FLAG>` environment variable (for example
`COB_PORT=5000`), or in `~/.config/co-claude-bridge/config.json` using camelCase keys.
Flags win over environment variables, which win over the file.

| Flag | Default | |
| --- | --- | --- |
| `--port` | `4141` | Port to listen on. |
| `--host` | `127.0.0.1` | Address to bind. Keep it on localhost: the bridge doesn't check the client's API key. |
| `--account-type` | `individual` | `business` or `enterprise` for those Copilot plans. |
| `--model` | newest Opus | Main model slot. |
| `--small-model` | `claude-haiku-4.5` | Helper slot, used for titles, summaries and haiku requests. |
| `--prefill` | `auto` | `auto` repairs only for models that reject prefill. `convert` and `drop` always repair. `off` never does. |
| `--alias FROM=TO` | | Map any model ID to a Copilot ID. Repeatable. The config file key is `modelAliases`. |
| `--strict-models` | off | Return an error for an unknown ID instead of using the family slot. |
| `-v`, `-vv` | | One line per request. `-vv` adds debug output. |
| `--log-bodies <dir>` | | Write each request, translated request and upstream response to numbered files, so a failing request can be replayed. Tokens are never written. |
| `--rate-limit <s>` | | Minimum gap between upstream calls. Adding `--wait` queues requests instead of returning 429. |
| `--github-token <t>` | | Use this GitHub OAuth token instead of the saved one. |

## Verbose log

```
16:27:29 #1 POST /v1/messages claude-opus-5.5 msgs=2 last=system tools=14 stream system_folded=1 → 200 2.1s in=18233 out=12
16:27:38 #3 POST /v1/messages claude-sonnet-5-5 → claude-sonnet-5.5 (normalized) msgs=2 last=user tools=0 → 200 0.8s in=412 out=37
16:27:40 #4 POST /v1/messages claude-opus-5.5 msgs=4 last=assistant tools=16 prefill=converted → 200 3.0s in=19020 out=88
```

Each line shows the model Claude Code asked for, what it resolved to, the role of the
last message as sent, and any repair applied. Failed requests are always logged, with
Copilot's error text, even without `-v`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Claude Code still goes to Bedrock or Vertex | `CLAUDE_CODE_USE_BEDROCK` or `CLAUDE_CODE_USE_VERTEX` is set, possibly under `env` in `~/.claude/settings.json`. `co-claude-bridge doctor` finds both. |
| `This GitHub account has no Copilot access` | Check that Copilot is enabled for the account you signed in with. Run `co-claude-bridge auth` to switch accounts. |
| 404 `isn't available on this Copilot account` | Use an ID from `co-claude-bridge models`, or add `--alias`. |
| The prefill 400 still appears | Run with `-v` and send the log line. `--prefill convert` forces the repair for every model. |
| Odd replies after `/resume` of an old session | Run `/clear`. Interrupted sessions can carry broken turns. |

## How it works

```
Claude Code ──/v1/messages──▶ co-claude-bridge ──/chat/completions──▶ api.githubcopilot.com
                              1. resolve the model against /models
                              2. fold system messages, repair prefill
                              3. translate Anthropic → OpenAI
                              4. call Copilot (token refresh, retries)
                              5. translate the reply or stream back
```

```
src/
  cli.ts, config.ts, log.ts, paths.ts
  auth/       GitHub device flow, token storage (0600), Copilot token refresh
  copilot/    Copilot headers, HTTP client with retries, SSE reader, model cache
  pipeline/   resolve-model, repair (system fold + prefill), prefix-strip,
              to-openai, from-openai, stream, count-tokens, errors
  server/     HTTP routing, the bridge itself, body logging
  commands/   start, auth, models, env, doctor
```

No runtime dependencies: only Node's own `http`, `fetch` and `crypto`.

The design draws on [ericc-ch/copilot-api](https://github.com/ericc-ch/copilot-api) (the
GitHub device flow and Copilot headers) and
[supastishn/claude-code-proxy](https://github.com/supastishn/claude-code-proxy) (mapping model
families to slots). No code is shared with either.

## Development

```bash
npm install
npm test            # unit + end-to-end tests against a mock Copilot server (Node 22.18+)
npm run typecheck
npm run build       # dist/, runs on Node 20+
node src/cli.ts -v  # run from source
```

`test/helpers/mock-copilot.ts` copies the Copilot behaviour that matters here: model
IDs, `model_not_supported`, the prefill 400 for Claude 4.6+ models, bearer-token expiry,
and streaming with a trailing usage chunk. To check Claude Code end to end without a
Copilot account, point the bridge at that mock with
`--copilot-token test-token --copilot-base-url <mock url>`.
