# Charter: co-claude-proxy

Status: **draft for the owner to edit and approve** (2026-11-12). Sources: README, docs/HANDOVER.md,
docs/SETUP.md, CONTRIBUTING.md, and the `agents/lab` findings (`lab/INDEX.md`). The repo has no decision
register or directives file yet, so anything not stated in those sources is marked `(proposed)`.

## 1. Objective

Run Claude Code on a GitHub Copilot subscription through a local proxy (`ericc-ch/copilot-api`), with
`copilot_shim` in front of it fixing what Copilot rejects or mistranslates, so a normal Claude Code
session works without errors.

## 2. Demo

`(proposed)` One command runs real Claude Code, headless, on a few coding tasks through the shim:

```bash
python3 demo/swebench_lite.py --n 5            # real: claude -p -> shim :4142 -> copilot-api :4141
python3 demo/swebench_lite.py --n 3 --fake     # offline: same flow against tests/fake_upstream.py
```

- **Dataset** `(proposed)`: SWE-bench Lite (300 real GitHub issues from 11 Python repos, MIT licence;
  Jimenez et al., "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?", ICLR 2024). It is the
  standard benchmark for coding agents such as Claude Code. A fetch script pins the dataset revision and
  checksum and picks a fixed list of instance IDs; no data is committed.
- **What the owner sees**: per task, the number of turns and requests, each shim `action=` line
  (`stripped`, `system_folded=N`, prompt-too-long rewrite), any failed turn with its upstream error, and
  whether Claude Code produced a patch. A final line such as `5/5 sessions, 0 failed turns`. The proxy
  is judged on sessions completing, not on patch correctness (the SWE-bench harness is optional).

## 3. Success criteria

1. `python3 -m pytest -q` passes offline on Python 3.10 and 3.13 (CI), with no secret or real endpoint.
2. Every request shape in `tests/fixtures/prefill_requests.json` fails directly against the fake and
   succeeds through the shim in `strip` and `hint` mode (exists today: 93 passed).
3. `(proposed)` The demo on a real Copilot login: 5/5 SWE-bench Lite sessions with 0 turns failing on a
   proxy-caused 4xx/5xx (prefill, prompt cap, output cap, schema). The owner records the run in docs/DEMO.md.
4. `(proposed)` The offline demo (`--fake`) runs in CI and reports 0 failed turns.
5. `(proposed)` A burst of 16 concurrent requests through the shim shows no stall over 1 s
   (the lab measured 1-28 s stalls with the default listen backlog of 5).

## 4. Current milestone: M1 "One real session, end to end" `(proposed)`

**Why first:** everything so far was built and tested without a Copilot login (HANDOVER, status
2026-11-05). The lab found defects that break a whole session (prompt-cap wording, output cap). Until
one real session runs through the shim, every other fix is a guess about Copilot's behaviour.

Acceptance criteria:

1. `python3 -m pytest -q` passes, including new tests for the lab's prompt-limit findings: the OpenAI
   `context_length_exceeded` wording is rewritten and a 400 that only quotes the phrase is not
   (`python3 -m pytest -q -k prompt_limit`).
2. `python3 demo/fetch_swebench_lite.py --verify` downloads the pinned subset and checks its checksum.
3. `python3 demo/swebench_lite.py --n 3 --fake` exits 0 and prints `3/3 sessions, 0 failed turns`.
4. Owner, with a login: `python3 -m copilot_shim.probe --json > probe.json` runs, and its verdicts are
   copied to docs/HANDOVER.md Findings.
5. Owner, with a login: `python3 demo/swebench_lite.py --n 5` prints `5/5 sessions, 0 failed turns`;
   the output goes in docs/DEMO.md, and any failing request is added (credentials removed) as a fixture.

**Next:** M2 "Tool payloads survive translation": tool images, `tool_reference`, `web_search`, 429
`retry-after`, `--max-output`, each from a lab finding with a test. M3 "Thinking and effort reach the
model": native Messages route, pending the owner's decision (question 1).

## 5. Non-goals

- Not supported by GitHub (README). No help evading Copilot quotas, rate limits or terms `(proposed)`.
- No model training, fine-tuning or hosting, and no measurement of model quality for its own sake.
- No upstream other than Copilot via copilot-api `(proposed)`, and no Anthropic API key path.
- No credentials, tokens or real account data in the repo, tests, fixtures or logs.

## 6. Constraints

- **Language**: the shim is standard-library Python 3.10+; only test tooling goes in
  `requirements-dev.txt` (one entry per line, sorted). Demo scripts may use `datasets` `(proposed)`.
- **Platforms**: Linux and macOS desktops running Claude Code, Node (`npx copilot-api`) and Python
  `(proposed: Windows untested)`. The shim binds `127.0.0.1` by default.
- **Tests**: offline, against `tests/fake_upstream.py`; never a real endpoint, login or secret.
- **GPU**: none needed or used `(proposed)`; all inference is on Copilot.
- **Budget**: each real run spends Copilot premium requests (the probe costs a couple per model).
  `(proposed)` Real-Copilot runs only by the owner, at most ~50 requests per demo run.
- **Process**: CONTRIBUTING.md (one concern per PR, under ~400 lines, owner approval, squash merge).

## 7. Open questions for the owner

1. **Native Messages route?** copilot-api 0.7.0 drops `thinking` and `effort` on every turn; Copilot's
   native Messages endpoint keeps them (lab: thinking-fields). *Recommendation:* make it M3, behind a
   flag, after M1 confirms which endpoint the account can reach.
2. **Shim vs. bridge?** The unmerged `claude/project-thread-iz93d9` branch is a standalone TypeScript
   server (`co-claude-bridge`) that replaces copilot-api. *Recommendation:* keep the shim + copilot-api
   as the product through M1; decide on the bridge with the M1 run data in hand.
3. **Demo dataset?** SWE-bench Lite needs a login and spends requests. *Recommendation:* approve a fixed
   5-instance subset for real runs, plus a `--fake` mode with the same tasks for CI.
