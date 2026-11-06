# Setup: Claude Code through copilot-api, with the prefill shim

```
claude  ->  copilot-shim :4142  ->  copilot-api :4141  ->  api.githubcopilot.com
```

`copilot-api` (`ericc-ch/copilot-api`) turns a GitHub Copilot subscription into an
Anthropic-compatible endpoint. Claude 4.6+ models behind Copilot reject any request
whose last message is from the assistant (`400: This model does not support assistant
message prefill`). `copilot-shim` (this repo) sits in front of copilot-api and removes
that trailing assistant message for models that reject it. It also logs the model and
last-message role of every request, so you can see which call was sending prefill.

Requirements: Node (for `npx`), Python 3.10+ (standard library only), Claude Code, and
a Copilot seat. Do not commit tokens. copilot-api stores its GitHub login in your home
directory, and nothing in this repo needs a credential.

> **Terms of use.** copilot-api and similar proxies are reverse-engineered and not
> supported by GitHub. Check GitHub's terms, and your employer's policy if the seat is
> work-provided, before you use one.

## 1. Start copilot-api

```bash
npx copilot-api@latest start            # first run walks through GitHub device login
# add --verbose to log upstream requests (check `npx copilot-api@latest start --help`)
```

It listens on `http://localhost:4141`.

## 2. List model IDs and probe prefill support

Probe copilot-api directly (port 4141), not the shim:

```bash
python3 -m copilot_shim.probe --list     # valid model IDs for this account
python3 -m copilot_shim.probe            # probe every claude* model
python3 -m copilot_shim.probe --model claude-opus-5.5 --model claude-haiku-4.5
python3 -m copilot_shim.probe --json > probe.json
```

Each model gets two requests with `max_tokens: 8`: one ending in a user turn
(`baseline`) and one ending in an assistant turn (`prefill`). Verdicts:

| verdict           | meaning                                                                 |
|-------------------|-------------------------------------------------------------------------|
| `accepts-prefill` | both 200. The shim can pass this model through unchanged.               |
| `rejects-prefill` | baseline 200, prefill 400 mentioning "prefill". The shim must rewrite.  |
| `model-unusable`  | baseline 4xx, e.g. `model_not_supported`. Don't configure this ID.      |
| `error`           | anything else (5xx, network). Re-run, or check the copilot-api log.     |

The probe costs a couple of premium requests per model. Results from 2026-11 are in
`docs/HANDOVER.md`: only `claude-haiku-4.5` accepted prefill.

## 3. Start the shim

```bash
python3 -m copilot_shim                                 # :4142 -> http://localhost:4141, mode=strip
python3 -m copilot_shim --mode hint                     # keep the prefill's intent (see below)
python3 -m copilot_shim --prefill-ok claude-haiku-4.5 --prefill-ok 'claude-sonnet-4.5'
python3 -m copilot_shim --map claude-haiku-4-5-20251001=claude-haiku-4.5
```

| flag                | default                 | effect |
|---------------------|-------------------------|--------|
| `--port`            | `4142`                  | listen port (binds `127.0.0.1`; change with `--host`) |
| `--upstream`        | `http://localhost:4141` | copilot-api base URL |
| `--mode`            | `strip`                 | `strip`: drop the trailing assistant message(s). `hint`: drop them and add their text to the last user turn as "Begin your reply with exactly ...". `off`: log only, change nothing. |
| `--prefill-ok GLOB` | `claude-haiku-4.5`      | models that accept prefill. Their requests pass unchanged. Repeatable. `--prefill-ok ''` rewrites for every model. |
| `--map FROM=TO`     | none                    | rename a model ID before forwarding. Use it when Claude Code sends an ID that `--list` doesn't show. |
| `-v`                |                         | debug logging |

Only `POST` bodies to `/v1/messages`, `/v1/messages/count_tokens` and
`/v1/chat/completions` are inspected. Everything else, including SSE streams, passes
through as is. Each inspected request logs one line:

```
INFO /v1/messages model=claude-sonnet-5.5 last_role=assistant stream=True action=stripped removed=1
```

Upstream errors (4xx/5xx) are logged with the first 500 bytes of the response body.

**Trailing `role: "system"` messages.** Claude Code 2.1.x sends its environment
block as a `role: "system"` entry at the end of `messages`. copilot-api translates
every non-user message into an assistant turn
([copilot-api#272](https://github.com/ericc-ch/copilot-api/issues/272)), so without
the shim even a plain "hello" fails with the prefill 400. For `/v1/messages` the
shim moves these entries into the top-level `system` field, for every model, and
logs `system_folded=N`:

```
INFO /v1/messages model=claude-opus-5.5 last_role=system stream=True action=none system_folded=1
```

**Strip vs hint.** `strip` is the safest change: the request becomes the conversation
up to the last user turn. The cost is that the reply no longer starts from the
prefill. If a helper call prefilled `{` to force JSON, the model may now reply with
prose and the helper may fail. Helper calls are non-essential, but `--mode hint` keeps
most of that intent. For an interrupted main-loop turn, `strip` means the model
regenerates its reply instead of continuing it.

## 4. Point Claude Code at the shim

```bash
export ANTHROPIC_BASE_URL=http://localhost:4142 ANTHROPIC_AUTH_TOKEN=dummy \
  ANTHROPIC_MODEL=claude-opus-5.5 \
  ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-5.5 \
  ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet-5.5 \
  ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-4.5 \
  ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4.5 \
  DISABLE_NON_ESSENTIAL_MODEL_CALLS=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
unset CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX
claude
```

`ANTHROPIC_AUTH_TOKEN=dummy` is a placeholder: copilot-api uses its own GitHub login.
Take every model ID from `probe --list`. Keeping the helper slots on
`claude-haiku-4.5` means helper calls that prefill work unchanged even without the
shim.

Check that `CLAUDE_CODE_USE_BEDROCK` isn't also set in `~/.claude/settings.json` or the
project's `.claude/settings*.json` under `env`. If it is, Claude Code goes to Bedrock
instead of the proxy.

## 5. Verify

1. In Claude Code, run `/clear`. This drops any interrupted turn that would otherwise
   be resent as prefill. Then send `say hi`.
2. Run `/status` and confirm the base URL is `http://localhost:4142` and the models are
   the ones you set.
3. Watch the shim log:
   - `action=stripped` / `action=hinted` lines show which model and endpoint were
     sending prefill. Record them in `docs/HANDOVER.md`.
   - With `--mode off` you get the same log lines without any rewriting. Use this to
     confirm the diagnosis before relying on the fix.

## Troubleshooting

| symptom | cause / fix |
|---|---|
| `This model does not support assistant message prefill` on a plain "hello" | Shim older than the `system_folded` fix: update it. Claude Code ends the request with a `role: "system"` message, which copilot-api turns into an assistant turn. |
| `This model does not support assistant message prefill` still appears | Claude Code isn't going through the shim (check `ANTHROPIC_BASE_URL` and `/status`). Or the model matches a `--prefill-ok` glob but rejects prefill: re-run the probe. |
| `model_not_supported` | The ID isn't valid for this account. Use an ID from `probe --list`, or add `--map`. The shim log shows the exact ID Claude Code sent. |
| `502 ... upstream unreachable` from the shim | copilot-api isn't running on `--upstream`. |
| Requests go to Bedrock | `CLAUDE_CODE_USE_BEDROCK` is set in the environment or a settings file. |

## Development

```bash
python3 -m pytest -q
```

The tests replay the failing request shapes in `tests/fixtures/prefill_requests.json`
against `tests/fake_upstream.py`. The fake reproduces the Copilot behaviour recorded in
the handover: a model list, `model_not_supported`, and the prefill 400 for every model
except `claude-haiku-4.5`. Each fixture must fail directly against the fake and succeed
through the shim, in both `strip` and `hint` mode. Apart from the handover's own probe
request, the fixtures are synthetic. When you capture a real failing request (shim
`--mode off -v`, or copilot-api `--verbose`), add it there with credentials removed.
