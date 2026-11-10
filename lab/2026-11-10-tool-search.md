# 2026-11-10: Claude Code's deferred tools (ToolSearch) through copilot-api

## Question

Claude Code 2.1.304+ turns on tool search by default even behind an `ANTHROPIC_BASE_URL`
gateway (see Sources). With tool search on, MCP tools and several built-ins (WebFetch,
WebSearch, NotebookEdit, Cron*, …) are *deferred*. The model has to call `ToolSearch`
before it can use them. On Anthropic's API this depends on two features: `defer_loading:
true` tool definitions and `tool_reference` blocks in tool results, which the API expands
into full definitions. copilot-api translates every request to OpenAI Chat Completions,
which has neither feature.

> With the shim in front of copilot-api, does an MCP or deferred built-in tool still work
> end to end? What exactly does Copilot receive? And what changes with
> `ENABLE_TOOL_SEARCH=false`, which would load every tool upfront: tool count, name
> length, payload size?

No earlier lab note looked at tools. copilot-translate and prefill-source looked only at
message roles.

## Method

- **Client:** Claude Code **2.1.321**, the native binary in `.agent-work/cc`, the same one
  the earlier labs used. It ran headless (`-p`) with a throwaway `HOME`, no credentials,
  and `ANTHROPIC_MODEL=claude-opus-5.5`.
  - MCP servers were limited to `--mcp-config … --strict-mcp-config`, with tools allowed
    by `--allowedTools 'mcp__lab__*'`.
- **MCP server:** `lab/tool-search/mcp_server.py`, a dependency-free stdio MCP server.
  - It exposes N synthetic tools (`lab_tool_NN`) plus three long names. After the
    `mcp__lab__` prefix these are 50, 65 and 80 characters long.
  - The schemas use `format`, `minimum`, `default` and `additionalProperties`.
- **Upstream:** `lab/tool-search/capture_server.py`. It is prefill-source's fake
  Anthropic endpoint plus a `PLAN:` trigger: the fake model calls the listed tools one
  per turn, in order.
  - Every body is recorded.
  - In every case below, Claude Code finished with `is_error=false` and "done.".
- **Cases** (`lab/tool-search/run.sh`):

  | case | MCP tools | ENABLE_TOOL_SEARCH | plan |
  |---|---:|---|---|
  | select40 | 40 (+3 long) | unset | `ToolSearch select:mcp__lab__lab_tool_03`, then call it |
  | keyword40 | 40 (+3) | unset | `ToolSearch "lab tool"`, call one; `ToolSearch select:WebFetch,<80-char name>`, call it |
  | select150 | 150 (+3) | unset | select one tool and call it |
  | off40 | 40 (+3) | `false` | call a tool and the 80-char tool directly |
  | off150 | 150 (+3) | `false` | call a tool directly |

- **Translation:** `lab/tool-search/translate_check.py` runs each body through
  `copilot_shim.rewrite` (default `Policy`, main @ 960daf7). It then runs the result
  through copilot-api 0.7.0's own `translateToOpenAI` (`0ea08feb`, bun 1.3.3, harness
  `lab/copilot-translate/translate.ts`).
  - Per-request numbers are in `lab/tool-search/translate_results.json`.
- No Copilot login was used, so Copilot's own validation is inferred from documented
  OpenAI limits and is not observed.

## Results

### 1. What Claude Code sends (Anthropic side)

| case | tools on 1st request | of which `defer_loading` | tool-def bytes (≈tokens at 4 B/token) | max name length |
|---|---:|---:|---:|---:|
| select40 / keyword40 / select150 | 12 | 1 (`DeferredToolPlaceholder`) | 30 350 (≈7.6k) | 23 |
| off40 | 65 | 0 | 94 596 (≈23.6k) | **80** |
| off150 | **175** | 0 | 158 276 (≈39.6k) | **80** |

- **Tool search on:** the first request is the same size for 40 and 150 MCP tools.
  - The MCP tools and 12 deferred built-ins appear only as names in the env
    `role:"system"` message ("The following deferred tools are now available via
    ToolSearch…").
- **After a ToolSearch call:** the next request carries:
  - a `tool_result` whose content is **only** `tool_reference` blocks, one per matched
    tool (1 for `select:`, 5 for the keyword query);
  - a sibling text block, `"Tool loaded."`;
  - the matched tools, added to `tools` with `defer_loading: true`, in alphabetical
    position. WebFetch landed at index 9 of 19.
- **Long names:** Claude Code 2.1.321 does **not** shorten MCP tool names to 64. It sent
  `mcp__lab__long_zzz…` at 80 characters. Its docs only flag names over 128.

### 2. What Copilot receives (after shim + copilot-api)

| check | select40 | keyword40 | select150 | off40 | off150 |
|---|---:|---:|---:|---:|---:|
| OpenAI `tools` (last request) | 13 | 19 | 13 | 65 | **175** |
| `defer_loading` survives | no | no | no | – | – |
| deferred tools exposed as ordinary functions | 2/2 | 8/8 | 2/2 | – | – |
| `DeferredToolPlaceholder` exposed as a callable function | yes | yes | yes | no | no |
| tool_reference-only results → `tool` message content `""` | 1/1 | 2/2 | 1/1 | – | – |
| names of found tools visible in any `tool` message | 0/1 | 0/7 | 0/1 | – | – |
| names breaking OpenAI's `^[a-zA-Z0-9_-]{1,64}$` | 0 | **1** (once loaded) | 0 | **2** | **2** |

What the model sees through Copilot after `ToolSearch`:

```
assistant  tool_calls: ToolSearch {"query":"select:mcp__lab__lab_tool_03",...}
tool       ""                 <- tool_reference blocks dropped by mapContent()
user       "Tool loaded."
```

Since `defer_loading` is dropped, the matched tool's full schema is in `tools` on the next
request. A model that calls the tool by name will therefore get a valid schema, and in
every case the fake model's call ran fine.

Three things still differ from first-party behaviour:

1. **Empty search result.**
   - Copilot never sees *which* tools a search found. For a keyword search (5 matches)
     the model gets `""` and has to spot the new entries in its tools list.
   - The API would instead expand the references in place, as `<functions>` text per the
     ToolSearch description.
   - Whether Copilot accepts an empty `tool` content is unverified. OpenAI accepts it.
2. **Prompt-cache churn (inference).**
   - On the API, deferred tools are stripped from the cached prefix. Through copilot-api
     they become ordinary functions inserted in the middle of `tools`, which is rendered
     before the messages. So every first load of a tool changes the prompt prefix.
   - This is the same mechanism and the same cost class as cache-prefix's sticky
     `role:"system"` updates: a full uncached re-read of the history per load. It assumes
     Copilot does prefix caching for Claude, as the cache-prefix lab did.
3. **The placeholder becomes a real function** ("never call this tool"). It is harmless
   unless the model calls it.

The `ENABLE_TOOL_SEARCH=false` alternative trades these for documented hard limits:

- 175 tools is over OpenAI's and VS Code Copilot's 128-tool cap.
- Any MCP name over 64 characters breaks OpenAI's function-name rule. With 40 tools the
  request carries ≈24k tokens of tool definitions; with 150 tools, ≈40k, on every request.
- Copilot's own limits for Claude models are unverified, but copilot-api passes both
  through unchanged.
- The same 64-character name problem appears with tool search **on**, as soon as a long
  name is loaded (keyword40 row).

Claude Code has a fallback: on a 400 mentioning `defer_loading`, it retries without
deferred tools (binary strings: "sending the request again without deferred tools").
That fallback **never fires** here, because copilot-api drops the field silently instead
of rejecting it.

## What the planner should do next

1. **Shim, small change (recommended):** before forwarding, replace each `tool_reference`
   block inside a `tool_result` with a text block naming the tool and including its
   definition.
   - Use the same `<functions>` line format the ToolSearch description promises, taken
     from the request's own `tools` entry.
   - This brings back the search result the model is told to expect, and it removes the
     empty `tool` content.
   - It is pure and testable with the fake upstream. Fixtures: capture shapes from
     `.agent-work/tool-search/t1/t2`, with no prompt text needed.
2. **Shim, optional:** drop `DeferredToolPlaceholder` from `tools` once at least one real
   deferred tool is present. Also consider whether to sort loaded deferred tools *after*
   the core tools so the existing prefix stays stable. That needs a cache-cost check like
   cache-prefix.
3. **Shim, optional:** shorten tool names longer than 64 characters (hash suffix) and map
   them back in responses, or at least log a warning.
   - Mapping back means rewriting the streamed `tool_use` names, so it is a larger
     change. Logging first is cheap.
4. **docs/SETUP.md:** say that tool search stays **on** through the shim, and that this
   is preferable. Do **not** recommend `ENABLE_TOOL_SEARCH=false` unless the user has
   fewer than ~100 tools in total and no MCP tool names over 64 characters.
5. **Live check (needs a Copilot login):**
   - one session with one MCP server: run `ToolSearch` and then call the tool;
   - one request with an 80-character tool name;
   - one request with more than 128 tools.

   Record Copilot's exact error texts, as compact-cap showed that the error format
   decides whether Claude Code recovers.

## Sources

- Claude Code docs, tool search: <https://code.claude.com/docs/en/agent-sdk/tool-search>.
  It is on by default behind an `ANTHROPIC_BASE_URL` gateway since v2.1.304, and the page
  lists the `ENABLE_TOOL_SEARCH` values.
- Anthropic API, tool search tool / `defer_loading` / `tool_reference`:
  <https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool>,
  <https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference>
- Claude Code MCP docs (gateway section, 128-character tool-name error):
  <https://code.claude.com/docs/en/mcp>
- OpenAI function-name rule `^[a-zA-Z0-9_-]{1,64}$` and the "string too long … maximum
  length 64" error:
  <https://community.openai.com/t/clarifying-tool-name-rules-for-mcp-and-openai-apis/1365988>,
  <https://community.openai.com/t/error-400-related-to-size-of-string/840368>
- 128 tools per request in VS Code Copilot:
  <https://blog.picassoia.com/mcp-tool-limit-in-cursor-claude-and-vs-code-how-many-tools-fit>
  (secondary source); GitHub's own write-up on cutting tools:
  <https://github.blog/ai-and-ml/github-copilot/how-were-making-github-copilot-smarter-with-fewer-tools/>
- copilot-api 0.7.0 `mapContent()` keeps only text, thinking and image blocks:
  `src/routes/messages/non-stream-translation.ts`.
  `translateAnthropicToolsToOpenAI()` copies only name, description and input_schema.
