# Handover: Claude Code via Copilot API proxy

**Goal:** Run Claude Code on a GitHub Copilot subscription via a local proxy (`ericc-ch/copilot-api`).

## Current state

- The proxy runs (`npx copilot-api@latest start`) at `http://localhost:4141`, and Claude Code reaches it. The 400s come back from `api.githubcopilot.com`, so auth and routing work.
- An earlier problem, Claude Code connecting to Bedrock, was probably `CLAUDE_CODE_USE_BEDROCK` set in the environment or a settings file. It looks resolved, since requests now hit the proxy.
- The remaining blocker is `400: This model does not support assistant message prefill`, plus one `model_not_supported` when a model ID didn't match.

## Findings

- Prefill test results, with a request ending in an assistant message:
  - `claude-haiku-4.5`: 200
  - `claude-haiku-5.5`, `claude-opus-4.8`, `claude-opus-5`, `claude-opus-5.5`, `claude-sonnet-5`, `claude-sonnet-5.5`: 400
- Public sources say Claude 4.6+ models reject any request ending in an assistant turn, and older ones (Haiku/Sonnet 4.5) accept it. This is a model limitation, not an admin policy.
- Unknown which request in the session sends prefill. Best guess: Claude Code's small/fast helper calls, which were mapped to `claude-sonnet-5.5`. Unconfirmed. A related Claude Code issue says it can also come from session state (interrupted turns), fixed by `/clear`.
- LiteLLM: no evidence found that it fixes this for the Copilot path, so not recommended yet.

## Next steps, in order

1. Restart Claude Code with the helper slots on the one model that accepts prefill:

   ```bash
   export ANTHROPIC_BASE_URL=http://localhost:4141 ANTHROPIC_AUTH_TOKEN=dummy \
     ANTHROPIC_MODEL=claude-opus-5.5 \
     ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-5.5 \
     ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet-5.5 \
     ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4.5 \
     ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-4.5 \
     DISABLE_NON_ESSENTIAL_MODEL_CALLS=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 && claude
   ```

2. Run `/clear`, then send a short prompt like "say hi". Use `/status` to confirm the base URL and models.
3. If it fails, start the proxy with `--verbose` (check `--help` for the flag) and capture the model and last message role of the failing request.
4. If the main loop itself sends prefill, fall back to `claude-haiku-4.5` as the main model, or open an issue on `ericc-ch/copilot-api` asking it to drop or patch a trailing assistant message for models that reject prefill. Alternative proxies: `copilot2api`, `first-officer` (check their issues for "prefill" first).

## Status update (2026-11-05)

These were built and tested on a server without a Copilot login, so they haven't been run against real Copilot yet:

- **Step 3 (capture):** `python3 -m copilot_shim --mode off` logs `model=… last_role=…` for every request without changing anything. This is the capture step without depending on copilot-api's `--verbose` flag.
- **Step 4 (fix):** `python3 -m copilot_shim` (default `--mode strip`) removes a trailing assistant message for every model except `claude-haiku-4.5`. `--mode hint` keeps the prefill's text as an instruction in the last user turn. This works whichever request sends prefill, helper or main loop, so you neither need to fall back to Haiku for the main model nor wait for an upstream fix.
- **Model IDs:** `python3 -m copilot_shim.probe --list` lists valid IDs. `python3 -m copilot_shim.probe` re-runs the prefill test above for every `claude*` model and separates `model_not_supported` from prefill rejection.

See `docs/SETUP.md`. Still to do with a Copilot login:

1. Run steps 1–2 above, pointed at the shim: `ANTHROPIC_BASE_URL=http://localhost:4142`.
2. Record which request(s) the shim log shows with `action=stripped`, and add a credential-free copy to `tests/fixtures/prefill_requests.json`.
3. Re-run the probe and update the Findings list if the model set has changed.
4. If `strip` breaks a helper (for example, a JSON title helper now replies in prose), try `--mode hint`.

## Caveats

- These proxies are reverse-engineered and unsupported by GitHub. Check GitHub's terms before using one on a work-provided Copilot seat.
- `model_not_supported` means a model ID wasn't valid for the account. Take IDs only from `http://localhost:4141/v1/models`.

## Sources

- https://docsearch.algolia.com/mcp/docs/repo/ericc-ch/copilot-api
- https://github.com/anomalyco/opencode/pull/14772
- https://claudeissues.com/issue/52433-bug-claude-code-prefilling-assistant-messages-when-using-claude-model-4-6
- https://www.agno.com/articles/fix-assistant-message-prefill-compatibility-for-claude-4-6-across-all-providers
- https://blog.laozhang.ai/en/posts/claude-opus-prefill-error-fix.md
- https://github.com/openbot-coder/litellm/pull/1

## Status update (2026-11-06)

- **Root cause of the 400 on a plain "hello":** Claude Code 2.1.318 ends its main request
  with a `role: "system"` message that holds the environment block. copilot-api turns
  every non-user message into an assistant turn
  ([copilot-api#272](https://github.com/ericc-ch/copilot-api/issues/272)), so the request
  reached Copilot ending on an assistant turn. This was confirmed by capturing the real
  request.
- **copilot-api and the Python shim are replaced** by co-claude-bridge (see the README).
  It is a standalone TypeScript bridge that signs in to GitHub and calls the Copilot API
  directly. It folds system messages, repairs prefill per model, and validates model IDs.
- **Still to do with a Copilot login:** run `npx co-claude-bridge doctor --probe`, then a
  Claude Code session with tools and a subagent, and record anything that fails here.
