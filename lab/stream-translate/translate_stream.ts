// Run OpenAI chat.completion.chunk streams through copilot-api's own
// translateChunkToAnthropicEvents(), exactly as handler.ts drives it (break on [DONE],
// skip empty data, fresh state per stream), and print the Anthropic SSE events.
// stdin: JSON {name: [chunk | "[DONE]", ...], ...}; stdout: JSON {name: [event, ...]}
// Usage: bun translate_stream.ts <copilot-api>/src/routes/messages/stream-translation.ts
const mod = await import(process.argv[2]);
const scenarios = JSON.parse(await Bun.stdin.text());
const out: Record<string, unknown> = {};
for (const [name, chunks] of Object.entries(scenarios as Record<string, unknown[]>)) {
  const state = { messageStartSent: false, contentBlockIndex: 0, contentBlockOpen: false, toolCalls: {} };
  const events: unknown[] = [];
  try {
    for (const c of chunks) {
      if (c === "[DONE]") break;
      events.push(...mod.translateChunkToAnthropicEvents(c, state));
    }
    out[name] = events;
  } catch (e) {
    out[name] = { throw: String(e), events };
  }
}
console.log(JSON.stringify(out));
