# 2026-11-09: Which Claude Code requests actually carry prefill?

## Question

HANDOVER's open question, and step 2 of its live-run checklist, is *which request in a
session sends prefill*. HANDOVER's best guess is "small/fast helper calls" or "interrupted
turns". The cache-prefix lab captured only headless `-p` runs, where Claude Code makes no
helper calls. So this lab asks:

> In an **interactive** Claude Code session, which requests end on an assistant turn,
> either as sent or after copilot-api's translation?
>
> The session covers helper calls, a subagent, an interrupt during streaming, an
> interrupt before the first token, a resumed session after an interrupt, and a `cd`.
>
> Does the answer depend on the HANDOVER env settings (helper slots mapped to Haiku 4.5,
> non-essential calls disabled)?

## Method

- **Client:** Claude Code **2.1.321**, the native linux-x64 binary from
  `.agent-work/cc/node_modules`, the same one cache-prefix used.
  - It runs as a real interactive TUI under a Python pty
    (`lab/prefill-source/drive.py`), with `--permission-mode acceptEdits`.
  - It uses a throwaway `HOME` with onboarding and folder trust pre-accepted.
  - No credentials. `ANTHROPIC_BASE_URL` is `127.0.0.1`.
- **Upstream:** `lab/prefill-source/capture_server.py`, a fake Anthropic endpoint.
  - It records every body and answers with canned SSE.
  - Triggers in the prompt make it return a `Read` tool_use, an `Agent` (Explore)
    tool_use or a `Bash` tool_use.
  - The `SLOW` trigger streams 60 deltas over 15 s, so the driver can press Esc
    mid-stream.
  - The `DELAY` trigger waits 6 s before sending any byte, so the driver can press Esc
    before the first token.
- **Scripts:**
  - **session:** hello, then Read, then Agent subagent, then SLOW + Esc, then a new
    prompt, then SLOW + Esc, then exit.
  - **resume:** `claude --continue` after the interrupted exit, then one prompt.
  - **extra:** `--continue`, then DELAY + Esc at 1.5 s, then a new prompt, then
    `Bash cd sub`, then a new prompt.
- **Env profiles:**
  - `handover`: HANDOVER next-step 1, verbatim.
  - `mapped-essential-on`: same helper-slot mapping, but without the two `DISABLE_*`
    flags.
  - `minimal`: only `ANTHROPIC_MODEL=claude-opus-5.5`.
- **Analysis:**
  - `summarize.py` runs `copilot_shim.rewrite` (main @ 960daf7, default `Policy`) on
    every body.
  - `translate_check.py` then runs the `off` and `strip` outputs through copilot-api
    0.7.0's own `translateToOpenAI` (`0ea08feb`, via bun 1.3.3 and
    `lab/copilot-translate/translate.ts`). It reports the last non-system OpenAI role,
    which is what Copilot's prefill check sees.
  - Results are in `capture_summary.json` (roles, block types and models only, no prompt
    text) and `translate_results.json`.

```bash
BIN=.agent-work/cc/node_modules/@anthropic-ai/claude-code-linux-x64/claude W=$PWD/.agent-work/prefill-src
for p in handover minimal mapped-essential-on; do
  python3 lab/prefill-source/drive.py $BIN $W/$p $W/$p.jsonl $p
  python3 lab/prefill-source/drive.py $BIN $W/$p $W/$p.jsonl $p resume; done
for p in minimal handover; do python3 lab/prefill-source/drive.py $BIN $W/$p $W/$p-extra.jsonl $p extra; done
python3 lab/prefill-source/summarize.py . $W/{handover,minimal,mapped-essential-on,minimal-extra,handover-extra}.jsonl
python3 lab/prefill-source/translate_check.py $PWD $PWD/.agent-work/copilot-api $W/*.jsonl
```

## Results

55 `/v1/messages` requests in total: 40 main-loop, 3 subagent and 12 helper. There were
no `count_tokens` calls and no GETs.

| | requests | Anthropic body ends on `assistant` (true prefill) | shim `action=stripped` | ends on `role:"system"` | OpenAI payload ends on `assistant`, `off` | same, `strip` |
|---|---:|---:|---:|---:|---:|---:|
| main loop | 40 | **0** | 0 | 5 | **5** | 0 |
| subagent (Explore) | 3 | **0** | 0 | 3 | **3** | 0 |
| helper (title) | 12 | **0** | 0 | 0 | 0 | 0 |
| **total** | **55** | **0** | **0** | **8** | **8** | **0** |

1. **Claude Code 2.1.321 never sent a true prefill.** No request in any profile or
   script ended on an Anthropic `assistant` message. The shim's strip path never fired.
   - **Interrupt mid-stream:** the next request carries the partial assistant text,
     then a user turn `[Request interrupted by user]` + the new prompt.
   - **Interrupt before the first token:** the next request has no assistant turn for
     the aborted call. The aborted prompt is **merged with the next prompt and no
     separator** (`"DELAY think firstafter early interrupt"`). This is a Claude Code
     quirk, not a proxy concern.
   - **Resume after an interrupted exit:** Claude Code inserts a synthetic assistant
     `"No response requested."` and then the new user turn, so the body ends on `user`.
2. **The prefill 400s that HANDOVER records come from trailing `role:"system"`
   messages.** copilot-api#272 turns these into OpenAI `assistant` turns. Without the
   shim (`off`), 8/55 payloads end on `assistant`, and each one is a request that ends
   on `role:"system"`:
   - the **first main-loop request** of every new session (`[user, system(env)]`): 3/3
     sessions;
   - the **first request of every subagent** (`[user, system(env)]`): 3/3;
   - the **request right after a `cd`** (`… user(tool_result), system("# Environment
     update")`): 2/2. This shape is new; cache-prefix saw it only in `-p` mode, and
     `tests/test_system_role.py` doesn't cover it.

   The shim's `_fold_system_messages` fixes all 8 (`strip`: 0/55). It folded at least
   one system message in all 43 main and subagent requests.
3. **Helper calls need no prefill fix.** The only helper Claude Code made was a
   session-title generator: no tools, a single `user` message, one per prompt in a new
   session, none after `--continue`. It never prefilled.
   - In `handover` (`DISABLE_NON_ESSENTIAL_MODEL_CALLS=1`) it doesn't run at all.
   - With the slots mapped, it goes to `claude-haiku-4.5`. Unmapped (`minimal`), it uses
     **the main model `claude-opus-5.5`**, not a built-in default ID. So the minimal env
     sent only one model ID, and none of these requests can cause `model_not_supported`.
   - The Explore subagent also used `claude-opus-5.5` in all three profiles.

**What this means for the milestone.** For 2.1.321, HANDOVER's helper-slot remapping
and the two `DISABLE_*` flags aren't needed to avoid prefill errors. The only fix that
matters is the system-message fold. In the live run the shim log should show
`system_folded=…` on most requests and almost never `action=stripped`.

**Limits:**
- One client version, and a fake model.
  - A real model can produce paths the fake never triggers: thinking blocks,
    `max_tokens` cut-offs, refusals, tool errors.
  - Other untested sources: `/compact` (the untracked compact-cap work covers it),
    MCP tools, plan mode, background tasks, and older Claude Code versions.
- The HANDOVER-era 400s may have come from an older client that did prefill. The
  claudeissues 52433 report concerns Claude 4.6-era clients.
- The interactive driver is timing-based. If a step lands late, its request can end up
  in the next step's bucket. The labels in `capture_summary.json` were checked by hand
  for the interrupt and `cd` steps.

## Sources

- Claude Code 2.1.321 (npm `@anthropic-ai/claude-code`), run locally against a fake
  endpoint.
- copilot-api 0.7.0 @ `0ea08feb`, `src/routes/messages/non-stream-translation.ts`;
  ericc-ch/copilot-api#272 (non-user roles become `assistant`).
- Earlier lab notes: `lab/2026-11-09-cache-prefix.md` (`-p` capture, sticky env
  updates) and `lab/2026-11-09-copilot-translate.md` (translation chain).
- HANDOVER sources on interrupted-turn prefill:
  https://claudeissues.com/issue/52433-bug-claude-code-prefilling-assistant-messages-when-using-claude-model-4-6

## What the planner should do next

1. **Rewrite the live-run checklist (docs/HANDOVER "Still to do", step 2).** Record
   `system_folded` as well as `action=stripped`.
   - Expected for 2.1.321: `system_folded≥1` on main and subagent requests, and
     `stripped` never or rarely.
   - Any `stripped` in the live log points to a path this lab did not exercise, and is
     worth a fixture.
   - Also note that HANDOVER step 1's Haiku remap and `DISABLE_*` flags are optional for
     2.1.321. Keep them as a fallback for older clients.
2. **Add regression tests** (small PR) for the two captured shapes that aren't covered
   yet:
   - a subagent's first request `[user, system]`, with the Explore tool set;
   - a post-`cd` `[…, assistant(tool_use), user(tool_result), system("# Environment
     update …")]`.

   Assert that both end on `user` after `rewrite` and on `user`/`tool` after
   translation.
3. **Optional follow-up lab:** repeat this capture with a Claude Code version from
   the HANDOVER era, if one can be identified. That would tell whether true prefill ever
   came from the client, or whether it was #272 all along.
