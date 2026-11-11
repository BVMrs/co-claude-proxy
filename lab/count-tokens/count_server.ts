// Serves copilot-api 0.7.0's own count_tokens route (its handler, translation and tokenizer,
// unchanged) with a fixed model list, so Claude Code's count requests get exactly the numbers
// copilot-api would return. No Copilot login or network needed.
// Usage: bun lab/count-tokens/count_server.ts <copilot-api checkout> <port> [model ids, comma-separated]
const [root, port, ids] = process.argv.slice(2)
const { Hono } = await import(`${root}/node_modules/hono/dist/index.js`)
const { messageRoutes } = await import(`${root}/src/routes/messages/route.ts`)
const { state } = await import(`${root}/src/lib/state.ts`)
const consola = (await import(`${root}/node_modules/consola/dist/index.mjs`)).default
consola.level = 1
// Copilot's model list gives Claude models tokenizer o200k_base; copilot-api falls back to it anyway.
state.models = {
  object: "list",
  data: (ids || "claude-opus-5.5,claude-sonnet-5.5,claude-haiku-4.5").split(",").map((id) => ({
    id, name: id, object: "model", vendor: "Anthropic", version: id, preview: false,
    model_picker_enabled: true,
    capabilities: { family: id, type: "chat", tokenizer: "o200k_base", object: "model_capabilities",
      limits: {}, supports: {} },
  })),
}
const app = new Hono()
app.route("/v1/messages", messageRoutes)
Bun.serve({ port: Number(port), hostname: "127.0.0.1", fetch: app.fetch })
console.error(`count_server on ${port}`)
