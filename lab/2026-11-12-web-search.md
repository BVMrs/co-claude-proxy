# What happens when Claude Code's WebSearch runs behind the shim and copilot-api?

Date: 2026-11-12 · Code: `lab/web-search/` · Reproduce: `lab/web-search/run_all.sh` (repo root)

## Question

Claude Code's `WebSearch` tool doesn't search locally. It sends a separate `/v1/messages`
sub-request that carries Anthropic's **server-side** tool `web_search_20250305`, and Anthropic's
API runs the search. Copilot has no server tools. With the shim on `main` plus copilot-api 0.7.0,
what does the user (and the main-loop model) actually get, and what should the shim do?

## Method

- Claude Code 2.1.321 (native binary, throwaway HOME, `-p`, `ENABLE_TOOL_SEARCH=false` so
  WebSearch isn't deferred), no network, no credentials.
- **Direct runs** (`run_case.sh` + `capture_server.py`): a fake Anthropic endpoint whose main
  loop calls `WebSearch {"query": …}`. The sub-request is answered four ways: like Anthropic
  (`server_tool_use` + `web_search_tool_result`), plain prose, a client `tool_use web_search`,
  or a 400.
- **Chain runs** (`run_chain.sh` + `copilot_sim.ts`): Claude Code → shim (`main` or the
  prototype) → a bun server that runs copilot-api 0.7.0's own `translateToOpenAI` and
  `translateChunkToAnthropicEvents`, the same way `handler.ts` does, in front of a scripted fake
  Copilot. Fake-Copilot answers to the sub-request:
  - `call`: calls `web_search` with `{}`
  - `callq`: calls it with `{"query": …}`
  - `text`: answers from memory
  - `reject`: a schema 400, relayed in copilot-api's `forwardError` shape
- `DENY=1`: the same chain with `permissions.deny: ["WebSearch"]` in user settings.

## Results

### The sub-request

Claude Code sends this sub-request (`subrequest.json`):

- model: the **main model** (`claude-opus-5.5`)
- `max_tokens`: 128000
- `tools`: `[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}]`
- system prompt: "Search once, then reply with a brief summary"
- one user turn: `Perform a web search for the query: <q>`

copilot-api 0.7.0's `translateAnthropicToolsToOpenAI` reads only `name`, `description` and
`input_schema`. So Copilot receives `{"type":"function","function":{"name":"web_search"}}`: no
description, no parameters (771-byte OpenAI body, `example_call_main.jsonl`). Copilot runs no
search for this function. The model can call it, or answer without it.

### What the main loop gets (chain, shim on `main`)

| fake Copilot | Copilot reqs | tool result the main model sees | error flagged? |
|---|---|---|---|
| `call` (`{}`) | 3 | `Web search results for query: "…"` + **nothing** + "REMINDER: You MUST include the sources above" | no |
| `callq` | 3 | same: empty results | no |
| `text` | 3 | `Web search results for query: "…"` + the model's **from-memory prose, with a made-up URL**, presented as search results | no |
| `reject` (400) | **4** (Claude Code retries once) | `API Error: 400 {"error":{"message":"Invalid schema …"}}` | yes (text only; copilot-api drops `is_error` in OpenAI `tool` messages) |

The direct runs give the same picture: Anthropic-style results become `Links: [...]` + summary.
Prose becomes "search results". A client `tool_use` becomes an empty result. A 400 becomes
`is_error=true` "API Error: 400 …", after 2 sub-requests.

So in **3 of the 4** plausible Copilot behaviours, WebSearch fails silently. The model is told it
has search results when it has none, or when it has the sub-model's guesses. The fourth costs 2
Copilot requests per search for an opaque schema error. Every case spends at least one extra
Copilot request (main model, `max_tokens` 128000) per search attempt.

### Prototype (`shim_ws.py`)

The prototype doesn't forward any `/v1/messages` body whose tools include a `web_search_*`,
`web_fetch_*` or `code_execution_*` server tool. It answers locally with a 400
`invalid_request_error`: "copilot-shim: web_search is an Anthropic server-side tool and GitHub
Copilot has no equivalent … use WebFetch on a known URL, or tell the user."

| fake Copilot | Copilot reqs | sub-requests reaching Copilot | main model sees |
|---|---|---|---|
| any of the 4 | **2** | **0** (2 answered locally, incl. the retry) | the explicit "unavailable, use WebFetch" error |

Results:

- 4/4 Copilot behaviours give the same explicit outcome.
- The run costs 1–2 fewer Copilot requests.
- Wall time is unchanged (≈0.7 s with the fake).
- The main-loop request and normal tools are untouched: the prototype is a thin wrapper around
  `copilot_shim.server` and changes no shim code.

### Workaround with no code

`permissions.deny: ["WebSearch"]` removes the tool from the main loop (22 → 21 tools, WebFetch
kept). Copilot gets 1 request and no sub-request, and the model never offers to search. This can
go in SETUP.md today.

## Limits

- The real Copilot response to a parameterless `web_search` function is **unconfirmed**: it may
  accept it (`call`/`text`) or reject it (`reject`). The prototype handles every case the same
  way, which is why it's recommended over guessing.
- Copilot's native Messages endpoint (open copilot-api PR #280, see the thinking-fields note)
  might pass `web_search_20250305` through unchanged. Whether Copilot runs Anthropic server
  tools there is unknown. If it does, the shim must not block it on that route.
- Tested with `ENABLE_TOOL_SEARCH=false`. With tool search on (the default), WebSearch is
  deferred until `ToolSearch select:WebSearch`, and then sends the same sub-request
  (tool-search note).
- The fake models are scripted. A real model given empty "results" may search again several
  times, and each try costs another Copilot request. Not measured.

## Sources

- Claude Code 2.1.321 binary: `web_search_20250305` and the sub-request captured in
  `subrequest.json`. The `Web search results for query` / `REMINDER: You MUST include the
  sources` wrapping was observed in the captures.
- copilot-api 0.7.0 (0ea08feb): `src/routes/messages/non-stream-translation.ts`
  `translateAnthropicToolsToOpenAI` (lines 231–245) and the `AnthropicTool` type (no `type`
  field); `handler.ts` for the driving order.
- Anthropic web search tool docs (`web_search_20250305`, server tool, `max_uses`):
  https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/web-search-tool
- Earlier lab notes: tool-search (2026-11-10), thinking-fields (2026-11-10), output-cap (2026-11-11).

## What the planner should do next

1. **Docs now:** add `"permissions": {"deny": ["WebSearch"]}` (or `--disallowedTools WebSearch`)
   to SETUP.md, and say that WebSearch can't work through Copilot while WebFetch does (WebFetch
   fetches locally).
2. **Shim change (small, ~25 lines):** in `_forward`, answer a messages request whose tools
   contain a server tool (`type` starting `web_search_`, `web_fetch_`, `code_execution_`)
   locally with a 400 `invalid_request_error` and an explanatory message, and log it. Port
   `lab/web-search/shim_ws.py`. Tests can use the fake upstream: assert 0 forwarded requests
   and the message text. Add a `--allow-server-tools` escape hatch for the native-route case.
3. **First live login:** send `subrequest.json` through copilot-api to Copilot once, and record
   whether Copilot accepts the parameterless function. That tells us which of the silent-failure
   rows real users hit today.
