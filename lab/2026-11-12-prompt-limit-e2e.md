# Prompt-limit rewrite (PR #10) end to end with real Claude Code: does it fix the session, and where does it misfire?

Date: 2026-11-12 · Branch tested: `claude/issue-9-shim-rewrite-copilot-prompt-token-limit` at 643b550 (PR #10), against `main` 960daf7

## Question

PR #10 adds `copilot_shim/errors.py`: a 400 whose body mentions Copilot's
"prompt token count of N exceeds the limit of M" (or the code `model_max_prompt_tokens_exceeded`)
is rewritten into Anthropic's `prompt is too long: N tokens > M maximum`. The point is that
Claude Code then compacts instead of breaking the session (lab note compact-cap, 2026-11-09).
Two questions: (1) with real Claude Code in the loop, does the PR turn the broken session into a working
one for every plausible wording of the limit error? (2) does the matcher fire on 400s that are **not**
about the prompt limit, and what does Claude Code do then?

## Method

All local: no credentials, no network, no model.

- `lab/prompt-limit-e2e/fake_copilot.py`: a fake upstream that speaks copilot-api's forwarded
  error shape (Copilot JSON stringified inside `{"error":{"message":"<json>","type":"error"}}`).
  Each prompt gets a *virtual* size (bytes/4 + 10k per `<<PAD>>` marker); above `CAP=128000` it
  rejects with one of 6 formats (`ERRFMT`): copilot (the canonical text), commas (`132,456`),
  codeonly (code but no numbers in the text), openai_ctx (OpenAI's `context_length_exceeded`
  wording), copilot413 (same body with HTTP 413), anthropic (native text, control).
  A request whose history contains `<<BAD>>` gets an **unrelated** 400 (`invalid_request_body`)
  whose message quotes the user's text, which here contains the phrase
  "prompt token count of 5 exceeds the limit of 4" (a user asking about the error, say).
- `run.sh`: real Claude Code 2.1.321 (`claude -p --continue`, 14 turns, +10k virtual tokens per turn)
  → `python -m copilot_shim` from a given checkout → fake. `matrix.sh` runs 19 configurations
  in parallel: direct (D), shim on main (M), shim on PR head (P), shim with a prototype matcher (F).
  `summarize.py` and `cost.py` read the request logs.
- `corpus.py`: 10 upstream 400 bodies run through `rewrite_prompt_too_long` directly, scored with
  Claude Code's own detection rule (from the 2.1.321 binary: overflow iff the message contains
  `prompt is too long` or `input is too long for requested model`; numbers via
  `prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)`), plus a robustness probe.
- `errors_fix.py`: a prototype matcher (see below). Dropped into a copy of the PR it passes all
  93 of the PR's own tests (PR head: 93/93 too).

## Results

### 1. The PR fixes the canonical case end to end

`matrix-out.txt` (14 turns each, cap reached at turn 12):

| run | shim | upstream error | requests | rejected | compactions OK | failed turns |
|---|---|---|---|---|---|---|
| D-copilot | none | copilot | 29 | 18 | 0 | 12, 13, 14 |
| M-copilot | main | copilot | 29 | 18 | 0 | 12, 13, 14 |
| **P-copilot** | **PR** | copilot | **16** | **1** | **1** | **none** |
| P-copilot-nousage | PR | copilot, no usage in stream | 16 | 1 | 1 | none |
| P-commas | PR | `132,456` | 16 | 1 | 1 | none |
| P-codeonly | PR | code, no numbers | 16 | 1 | 1 | none |
| P-413 | PR | HTTP 413 | 16 | 1 | 1 | none (CC treats 413 as overflow by itself) |
| P-anthropic | PR | native text | 16 | 1 | 1 | none |
| **P-openai_ctx** | PR | OpenAI `context_length_exceeded` | 29 | 18 | 0 | **12, 13, 14** |
| D-anthropic | none | native text (control) | 16 | 1 | 1 | none |

With the PR, the canonical Copilot error behaves exactly like Anthropic's native one: one rejected
request, one compaction, the session carries on (1.25M prompt tokens sent vs 3.45M on main, where
every later turn retries the over-cap prompt 6 times and fails). Without usage in the stream the
result is the same. Numbers with thousands separators are not parsed (`commas`: CC gets no
`N > M`), but compaction still happens, so this is cosmetic.

The one wording it misses is OpenAI's `This model's maximum context length is 128000 tokens.
However, your messages resulted in 132456 tokens` / `context_length_exceeded`: the session breaks
exactly as on main. Whether Copilot ever sends that wording for Claude models is unconfirmed (the
PR itself notes the Copilot wording is not confirmed on a live account either).

### 2. The matcher fires on unrelated 400s and wipes context (false positive)

The PR searches **every string** in the error body, including user text or tool descriptions that
an upstream validation error quotes back. Corpus (`corpus-out.txt`):

| case | PR | CC sees overflow? | verdict |
|---|---|---|---|
| canonical, commas, code-only, anthropic-native, 413 | rewritten / pass | yes | ok |
| unrelated-400, max-tokens | pass | no | ok |
| openai-ctx | pass | no | **false negative** |
| echo-user-text (400 quoting the user's message) | rewritten → `5 tokens > 4 maximum` | yes | **false positive** |
| echo-tool-desc (400 quoting a tool description) | rewritten | yes | **false positive** |

End to end (`turns-P-bad.txt` vs `turns-M-bad.txt`; turn 5 of a 52k-token, far-under-cap
conversation quotes the phrase and gets an unrelated 400):

- **main**: turn 5 fails with the real error text (`API Error: 400 … flagged sequence …`);
  history is untouched. Later turns fail the same way because the fake keeps rejecting any
  history with the marker (a harness artefact, same in M, P and F).
- **PR**: Claude Code is told "prompt is too long: 5 tokens > 4 maximum", **compacts a
  conversation at 52k of 128k twice in turn 5** (2 successful compactions: 62k → 33k → 22k
  history), then prints only `Prompt is too long`. Turns 6–14 each run 4–10 further compaction
  attempts: 62 compaction requests, 2.64M of the 3.57M prompt tokens sent are compaction calls.
  The user loses context for nothing and never sees the real error.
- **prototype fix (F-bad)**: identical to main (64 requests, 0 compactions, real error shown).

### 3. Robustness

A 100k-deep `[[[[…` body makes the PR's `rewrite_prompt_too_long` raise `RecursionError`
(Python's `json.loads`, caught only as `ValueError`), which escapes `_relay`; the client gets a
dropped connection instead of the upstream 400. Not a realistic Copilot body; a one-word fix
(`except (ValueError, RecursionError)`). Time per corpus case < 5 ms.

### Prototype matcher (`errors_fix.py`)

Matches only an error `message` field (at any depth, incl. JSON-in-string) that **starts** with
Copilot's phrase or OpenAI's context-length phrase, or a `code` field equal to
`model_max_prompt_tokens_exceeded` / `context_length_exceeded`; accepts thousands separators;
passes anything undecodable through. Corpus: 10/10 correct (`corpus-fix-out.txt`); e2e F-runs:
6/6 limit formats compact with 0 failed turns (incl. openai_ctx and commas, now with numbers),
F-bad = main; PR's 93 tests pass.

## Sources

- PR #10 diff, `copilot_shim/errors.py` and `server.py` `_relay` at 643b550.
- Claude Code 2.1.321 binary (npm `@anthropic-ai/claude-code`): overflow detection strings and the
  `N tokens > M` regex quoted above.
- copilot-api 0.7.0 `src/lib/error.ts` `forwardError` (the stringified-JSON error shape).
- OpenAI error wording for `context_length_exceeded` (the canonical Chat Completions text).
- Earlier lab notes: compact-cap (2026-11-09), output-cap (2026-11-11).

## What the planner should do next

1. Before merging PR #10, tighten the matcher: match the phrase only at the **start of an error
   `message` field** and the code only in a **`code` field**, not anywhere in the body. The
   prototype `lab/prompt-limit-e2e/errors_fix.py` does this and passes the PR's tests; add the
   `echo-user-text` / `echo-tool-desc` bodies from `corpus.py` as regression tests.
2. Same change: accept OpenAI's `context_length_exceeded` wording, and `1,234`-style numbers;
   catch `RecursionError` with `ValueError`.
3. On first live login, capture Copilot's real over-cap 400 (`--verbose`) and pin the wording in a test.
4. Reproduce: `TURNS=14 lab/prompt-limit-e2e/matrix.sh <claude-bin> <main> <pr> <outdir> [<fix>]`;
   `python3 lab/prompt-limit-e2e/corpus.py <checkout>`.

PR-FINDING #10: the matcher searches every string in the error body, so an unrelated 400 that quotes user text or a tool description containing "prompt token count … exceeds the limit" is rewritten to "prompt is too long"; real Claude Code 2.1.321 then compacts a 52k-of-128k conversation twice and hides the real error (62 compaction requests over 10 turns, vs 0 on main); OpenAI-style `context_length_exceeded` is not recognised (session still breaks, 3/3 turns fail); a deeply nested body raises RecursionError out of `_relay`. A start-of-message/code-field matcher (lab/prompt-limit-e2e/errors_fix.py) fixes all three and passes the PR's 93 tests.
