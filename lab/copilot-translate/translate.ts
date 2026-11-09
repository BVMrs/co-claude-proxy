// Feed Anthropic Messages bodies (JSONL on stdin) through copilot-api's own
// translateToOpenAI() and print the OpenAI payload it would POST to Copilot (JSONL).
// Usage: bun lab/copilot-translate/translate.ts <copilot-api>/src/routes/messages/non-stream-translation.ts
const mod = await import(process.argv[2]);
const lines = (await Bun.stdin.text()).split("\n").filter(Boolean);
for (const line of lines) {
  const body = JSON.parse(line);
  let out: unknown;
  try {
    out = { ok: mod.translateToOpenAI(body) };
  } catch (e) {
    out = { error: String(e) };
  }
  console.log(JSON.stringify(out));
}
