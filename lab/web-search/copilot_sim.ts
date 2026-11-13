// copilot-api 0.7.0 /v1/messages, driven exactly like src/routes/messages/handler.ts
// (translateToOpenAI -> "Copilot" -> translateChunkToAnthropicEvents / translateToAnthropic), with a
// scripted fake Copilot in place of api.githubcopilot.com. No credentials, no network.
//
// Fake Copilot behaviour (OpenAI side):
//   tools contain function "WebSearch" and no tool message yet -> call WebSearch {"query": QUERY}
//   tools contain function "web_search" (Claude Code's server-tool sub-request) -> COPILOT_WS:
//       call   : call web_search with "{}" (the translated function has no parameters)
//       callq  : call web_search with {"query": ...}
//       text   : answer in prose, no search ("from memory ...")
//       reject : 400 like copilot-api's forwardError would relay a Copilot schema rejection
//   anything else -> "done."
// Every translated OpenAI payload is appended to LOG as JSONL.
// Usage: COPILOT_WS=call bun copilot_sim.ts <copilot-api checkout> <port> <log.jsonl>
const [api, portS, logPath] = process.argv.slice(2);
const tr = await import(`${api}/src/routes/messages/non-stream-translation.ts`);
const st = await import(`${api}/src/routes/messages/stream-translation.ts`);
const MODE = process.env.COPILOT_WS ?? "call";
const QUERY = process.env.QUERY ?? "zork release year";
const fs = await import("node:fs");
let n = 0;

function copilot(p: any): { status: number; body?: any; chunks?: any[] } {
  const names = (p.tools ?? []).map((t: any) => t.function?.name);
  const hasTool = (p.messages ?? []).some((m: any) => m.role === "tool");
  const id = `chatcmpl-${++n}`;
  const base = { id, object: "chat.completion.chunk", created: 0, model: p.model };
  const call = (name: string, args: string) => [
    { ...base, choices: [{ index: 0, delta: { role: "assistant", tool_calls: [{ index: 0, id: `call_${n}`, type: "function", function: { name, arguments: "" } }] }, finish_reason: null }] },
    { ...base, choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: args } }] }, finish_reason: null }] },
    { ...base, choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }], usage: { prompt_tokens: 50, completion_tokens: 5, total_tokens: 55 } },
  ];
  const text = (t: string) => [
    { ...base, choices: [{ index: 0, delta: { role: "assistant", content: t }, finish_reason: null }] },
    { ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 50, completion_tokens: 5, total_tokens: 55 } },
  ];
  if (names.includes("web_search")) {
    if (MODE === "reject")
      return { status: 400, body: { error: { message: JSON.stringify({ error: { message: "Invalid schema for function 'web_search': In context=(), object schema missing properties", code: "invalid_function_parameters" } }), type: "error" } } };
    if (MODE === "call") return { status: 200, chunks: call("web_search", "{}") };
    if (MODE === "callq") return { status: 200, chunks: call("web_search", JSON.stringify({ query: QUERY })) };
    return { status: 200, chunks: text("I can't browse the web, but from memory: Zork was first released in 1977 (https://en.wikipedia.org/wiki/Zork).") };
  }
  if (names.includes("WebSearch") && !hasTool) return { status: 200, chunks: call("WebSearch", JSON.stringify({ query: QUERY })) };
  return { status: 200, chunks: text("done.") };
}

Bun.serve({
  port: Number(portS),
  hostname: "127.0.0.1",
  async fetch(req) {
    const url = new URL(req.url);
    if (req.method !== "POST") return Response.json({ data: [] });
    const body = await req.json();
    if (url.pathname.endsWith("/count_tokens")) return Response.json({ input_tokens: 100 });
    const oa = tr.translateToOpenAI(body);
    const res = copilot(oa);
    fs.appendFileSync(logPath, JSON.stringify({ t: Date.now(), anthropic_tools: body.tools?.map((t: any) => t.type ?? t.name), openai: oa, status: res.status }) + "\n");
    if (res.status !== 200) return new Response(JSON.stringify(res.body), { status: res.status, headers: { "content-type": "application/json" } });
    if (!oa.stream) {
      // Non-streaming: collapse chunks into one completion (only used by helpers).
      const content = res.chunks!.map((c) => c.choices[0].delta.content ?? "").join("");
      return Response.json(tr.translateToAnthropic({ id: "x", object: "chat.completion", created: 0, model: oa.model, choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop", logprobs: null }], usage: { prompt_tokens: 50, completion_tokens: 5, total_tokens: 55 } }));
    }
    const state = { messageStartSent: false, contentBlockIndex: 0, contentBlockOpen: false, toolCalls: {} };
    let out = "";
    for (const c of res.chunks!) for (const e of st.translateChunkToAnthropicEvents(c, state)) out += `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`;
    return new Response(out, { headers: { "content-type": "text/event-stream" } });
  },
});
