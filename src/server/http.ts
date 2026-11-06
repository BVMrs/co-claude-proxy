import http from "node:http";

import { log } from "../log.ts";
import { errorBody } from "../pipeline/errors.ts";
import { HttpError, type Bridge, type Reply } from "./bridge.ts";

const MAX_BODY = 64 * 1024 * 1024; // screenshots in long sessions add up

export function createServer(bridge: Bridge): http.Server {
  return http.createServer((req, res) => {
    handle(bridge, req, res).catch((e: Error) => {
      log.error(`unhandled error on ${req.method} ${req.url}: ${e.stack ?? e.message}`);
      if (!res.headersSent) sendJson(res, 500, errorBody(500, `co-claude-bridge internal error: ${e.message}`));
      else res.end();
    });
  });
}

async function handle(bridge: Bridge, req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  const url = new URL(req.url ?? "/", "http://localhost");
  const route = `${req.method} ${url.pathname.replace(/\/+$/, "") || "/"}`;
  const abort = new AbortController();
  res.on("close", () => {
    if (!res.writableFinished) abort.abort();
  });

  let reply: Reply;
  try {
    switch (route) {
      case "GET /":
      case "HEAD /":
        reply = { status: 200, json: { name: "co-claude-bridge", status: "ok" } };
        break;
      case "GET /health":
        reply = bridge.health();
        break;
      case "GET /v1/models":
        reply = await bridge.listModels();
        break;
      case "POST /v1/messages":
        reply = await bridge.messages(await readJson(req), abort.signal);
        break;
      case "POST /v1/messages/count_tokens":
        reply = await bridge.countTokens(await readJson(req));
        break;
      case "POST /v1/chat/completions":
      case "POST /chat/completions":
        reply = await bridge.chatPassthrough(await readJson(req), abort.signal);
        break;
      default:
        reply = { status: 404, json: errorBody(404, `co-claude-bridge has no route ${route}`) };
    }
  } catch (e) {
    if (abort.signal.aborted) return;
    if (e instanceof HttpError) {
      reply = { status: e.status, json: errorBody(e.status, e.message) };
    } else throw e;
  }

  if (reply.stream) {
    res.writeHead(reply.status, reply.headers ?? {});
    res.flushHeaders();
    await reply.stream((chunk) => {
      if (!res.writableEnded) res.write(chunk);
    }, abort.signal);
    res.end();
    return;
  }
  if (req.method === "HEAD") {
    res.writeHead(reply.status).end();
    return;
  }
  sendJson(res, reply.status, reply.json, reply.headers);
}

function sendJson(res: http.ServerResponse, status: number, body: unknown, headers: Record<string, string> = {}): void {
  const text = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(text), ...headers });
  res.end(text);
}

async function readJson(req: http.IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const c of req) {
    size += (c as Buffer).length;
    if (size > MAX_BODY) throw new HttpError(413, `Request body is over ${MAX_BODY / 1024 / 1024} MB.`);
    chunks.push(c as Buffer);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  if (!text) throw new HttpError(400, "Request body is empty.");
  try {
    return JSON.parse(text);
  } catch {
    throw new HttpError(400, "Request body is not valid JSON.");
  }
}
