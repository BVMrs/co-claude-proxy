// A fake api.githubcopilot.com that behaves like the real one where it matters:
// /models, model_not_supported, the prefill 400 for Claude 4.6+ models, bearer
// tokens, and OpenAI-style streaming with a separate trailing usage chunk.

import http from "node:http";
import type { AddressInfo } from "node:net";

import type { ChatRequest } from "../../src/types/openai.ts";

export const PREFILL_ERROR = "This model does not support assistant message prefill. The conversation must end with a user message.";

export const MODELS = [
  { id: "claude-opus-5.5", name: "Claude Opus 5.5", vendor: "Anthropic", capabilities: { type: "chat", supports: { tool_calls: true }, limits: { max_context_window_tokens: 200000 } } },
  { id: "claude-sonnet-5.5", name: "Claude Sonnet 5.5", vendor: "Anthropic", capabilities: { type: "chat", supports: { tool_calls: true } } },
  { id: "claude-haiku-4.5", name: "Claude Haiku 4.5", vendor: "Anthropic", capabilities: { type: "chat", supports: { tool_calls: true } } },
  { id: "gpt-5", name: "GPT-5", vendor: "OpenAI", capabilities: { type: "chat", supports: { tool_calls: true } } },
  { id: "text-embedding-3-small", name: "Embedding", vendor: "OpenAI", capabilities: { type: "embeddings" } },
];

const ACCEPTS_PREFILL = new Set(["claude-haiku-4.5", "gpt-5"]);

export interface Recorded {
  path: string;
  headers: http.IncomingHttpHeaders;
  body: ChatRequest;
}

export class MockCopilot {
  readonly received: Recorded[] = [];
  validTokens = new Set(["test-token"]);
  /** Models that answer 500 the next N times. */
  failNext = new Map<string, number>();
  /** Override the reply text. */
  reply?: (req: ChatRequest) => string;
  private server = http.createServer((req, res) => void this.handle(req, res));
  url = "";

  async start(): Promise<this> {
    await new Promise<void>((r) => this.server.listen(0, "127.0.0.1", () => r()));
    this.url = `http://127.0.0.1:${(this.server.address() as AddressInfo).port}`;
    return this;
  }

  async stop(): Promise<void> {
    this.server.closeAllConnections();
    await new Promise<void>((r) => this.server.close(() => r()));
  }

  last(): Recorded {
    return this.received.at(-1)!;
  }

  private async handle(req: http.IncomingMessage, res: http.ServerResponse) {
    const json = (status: number, body: unknown) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(body));
    };
    const token = String(req.headers.authorization ?? "").replace(/^Bearer /, "");
    if (!this.validTokens.has(token)) return json(401, { error: { message: "unauthorized: token expired" } });

    if (req.method === "GET" && req.url === "/models") return json(200, { data: MODELS, object: "list" });
    if (req.method !== "POST" || req.url !== "/chat/completions") return json(404, { error: { message: "not found" } });

    let raw = "";
    for await (const c of req) raw += c;
    const body = JSON.parse(raw) as ChatRequest;
    this.received.push({ path: req.url, headers: req.headers, body });

    if (!MODELS.some((m) => m.id === body.model)) {
      return json(400, { error: { message: "The requested model is not supported.", code: "model_not_supported" } });
    }
    const fails = this.failNext.get(body.model) ?? 0;
    if (fails > 0) {
      this.failNext.set(body.model, fails - 1);
      return json(500, { error: { message: "boom" } });
    }
    if (body.messages.at(-1)?.role === "assistant" && !ACCEPTS_PREFILL.has(body.model)) {
      return json(400, { error: { message: PREFILL_ERROR, code: "invalid_request_body" } });
    }

    const lastUser = [...body.messages].reverse().find((m) => m.role === "user");
    const userText = typeof lastUser?.content === "string" ? lastUser.content : (lastUser?.content ?? []).map((p) => (p.type === "text" ? p.text : "")).join("");
    const prefill = /<prefill>([\s\S]*?)<\/prefill>/.exec(userText)?.[1];
    const wantsTool = Boolean(body.tools?.length) && /use the tool/i.test(userText);
    const text = this.reply?.(body) ?? (prefill !== undefined ? `${prefill} continued` : "hi there");
    const usage = { prompt_tokens: 120, completion_tokens: 7, total_tokens: 127, prompt_tokens_details: { cached_tokens: 20 } };
    const toolCall = { id: "call_1", type: "function" as const, function: { name: body.tools?.[0]?.function.name ?? "tool", arguments: '{"path":"README.md"}' } };

    if (!body.stream) {
      return json(200, {
        id: "chatcmpl-1",
        model: body.model,
        choices: wantsTool
          ? [
              { index: 0, message: { role: "assistant", content: "Let me look." }, finish_reason: "stop" },
              { index: 1, message: { role: "assistant", content: null, tool_calls: [toolCall] }, finish_reason: "tool_calls" },
            ]
          : [{ index: 0, message: { role: "assistant", content: text }, finish_reason: "stop" }],
        usage,
      });
    }

    res.writeHead(200, { "content-type": "text/event-stream" });
    const send = (o: unknown) => res.write(`data: ${JSON.stringify(o)}\n\n`);
    const base = { id: "chatcmpl-1", model: body.model };
    if (wantsTool) {
      send({ ...base, choices: [{ index: 0, delta: { role: "assistant", content: "Let me look." } }] });
      send({ ...base, choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: "call_1", type: "function", function: { name: toolCall.function.name, arguments: "" } }] } }] });
      send({ ...base, choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: '{"path":' } }] } }] });
      send({ ...base, choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: '"README.md"}' } }] } }] });
      send({ ...base, choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }] });
    } else {
      // Split the text into small pieces so prefix stripping sees partial chunks.
      for (let i = 0; i < text.length; i += 3) send({ ...base, choices: [{ index: 0, delta: { content: text.slice(i, i + 3) } }] });
      send({ ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] });
    }
    send({ ...base, choices: [], usage });
    res.write("data: [DONE]\n\n");
    res.end();
  }
}
