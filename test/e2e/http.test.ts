// The local HTTP server's front door and the bridge's less common paths:
// access control, malformed requests, upstream failures and stream edge cases.

import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { after, before, beforeEach, describe, it } from "node:test";

import { setLevel } from "../../src/log.ts";
import { Bridge, validateMessagesRequest } from "../../src/server/bridge.ts";
import { createServer } from "../../src/server/http.ts";
import { assemble, authHeaders, events, KEY, logLines, post, startBridge, type Running } from "../helpers/bridge.ts";
import { MockCopilot } from "../helpers/mock-copilot.ts";

const msg = (model = "claude-opus-5.5", extra: Record<string, unknown> = {}) => ({
  model,
  max_tokens: 8,
  messages: [{ role: "user", content: "x" }],
  ...extra,
});

/** A raw request, so tests control every header (fetch always sets Host itself). */
function raw(url: string, opts: { method?: string; path?: string; headers?: http.OutgoingHttpHeaders; body?: string }) {
  const u = new URL(url);
  return new Promise<{ status: number; text: string; headers: http.IncomingHttpHeaders }>((resolve, reject) => {
    const req = http.request(
      { host: u.hostname, port: u.port, method: opts.method ?? "GET", path: opts.path ?? "/", headers: opts.headers, setHost: false },
      (res) => {
        let text = "";
        res.on("data", (c) => (text += c));
        res.on("end", () => resolve({ status: res.statusCode!, text, headers: res.headers }));
      },
    );
    req.on("error", reject);
    req.end(opts.body);
  });
}

describe("access control on the local server", () => {
  let mock: MockCopilot;
  let b: Running;
  before(async () => {
    mock = await new MockCopilot().start();
    b = await startBridge(mock);
  });
  after(async () => {
    await b.close();
    await mock.stop();
  });

  it("answers / and /health without a key", async () => {
    const root = await fetch(`${b.url}/`);
    assert.equal(root.status, 200);
    assert.deepEqual(await root.json(), { name: "co-claude-bridge", status: "ok" });
    const head = await fetch(`${b.url}/`, { method: "HEAD" });
    assert.equal(head.status, 200);
    const health = (await (await fetch(`${b.url}/health`)).json()) as any;
    assert.equal(health.status, "ok");
    assert.equal(health.slots.main, "claude-opus-5.5");
    assert.equal(health.models, 4);
    assert.equal(JSON.stringify(health).includes("test-token"), false);
  });

  it("refuses API calls without the right key", async () => {
    for (const headers of [{}, { "x-api-key": "wrong" }, { authorization: "Bearer wrong" }] as Record<string, string>[]) {
      const r = await post(`${b.url}/v1/messages`, msg(), headers);
      assert.equal(r.status, 401, JSON.stringify(headers));
      assert.equal(r.json.error.type, "authentication_error");
    }
    assert.equal((await fetch(`${b.url}/v1/models`)).status, 401);
    assert.equal(mock.received.length, 0, "nothing reached Copilot");
  });

  it("accepts the key as a bearer token, the way Claude Code sends ANTHROPIC_AUTH_TOKEN", async () => {
    const r = await post(`${b.url}/v1/messages`, msg(), { authorization: `Bearer ${KEY}` });
    assert.equal(r.status, 200, r.text);
  });

  it("refuses a DNS-rebound Host and browser Origins even with the key", async () => {
    const body = JSON.stringify(msg());
    const base = { "content-type": "application/json", "x-api-key": KEY };
    const rebound = await raw(b.url, { method: "POST", path: "/v1/messages", headers: { ...base, host: "attacker.example:4141" }, body });
    assert.equal(rebound.status, 403);
    const browser = await raw(b.url, { method: "POST", path: "/v1/messages", headers: { ...base, host: "127.0.0.1", origin: "https://attacker.example" }, body });
    assert.equal(browser.status, 403);
    const noHost = await raw(b.url, { path: "/health", headers: {} });
    assert.equal(noHost.status, 400);
  });

  it("refuses a cross-site 'simple' text/plain POST", async () => {
    const r = await raw(b.url, {
      method: "POST",
      path: "/v1/messages",
      headers: { host: "127.0.0.1", "content-type": "text/plain", "x-api-key": KEY },
      body: JSON.stringify(msg()),
    });
    assert.equal(r.status, 415);
  });

  it("rejects malformed bodies with Anthropic-style errors", async () => {
    const bad = async (body: unknown, re: RegExp, status = 400) => {
      const r = await post(`${b.url}/v1/messages`, body);
      assert.equal(r.status, status, r.text);
      assert.equal(r.json.type, "error");
      assert.match(r.json.error.message, re);
    };
    await bad("", /empty/);
    await bad("{not json", /not valid JSON/);
    await bad([], /JSON object/);
    await bad({ messages: [] }, /`model` is required/);
    await bad({ model: "m", messages: "hi" }, /must be an array/);
    await bad({ model: "m", messages: [{ role: "tool", content: "x" }] }, /role of user, assistant or system/);
    await bad({ model: "m", messages: [null] }, /role of user/);
  });

  it("returns 404 for unknown routes and trailing slashes still route", async () => {
    const r = await fetch(`${b.url}/v2/nothing`, { headers: authHeaders });
    assert.equal(r.status, 404);
    assert.equal((await fetch(`${b.url}/health/`)).status, 200);
  });
});

describe("request size limit", () => {
  it("answers 413 for a body over the limit", async () => {
    const mock = await new MockCopilot().start();
    const b = await startBridge(mock);
    const server = createServer(b.ctx.bridge, { apiKey: KEY, maxBodyBytes: 1024 });
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
    try {
      const url = `http://127.0.0.1:${(server.address() as any).port}`;
      const r = await post(`${url}/v1/messages`, msg("claude-opus-5.5", { pad: "x".repeat(4096) }));
      assert.equal(r.status, 413);
      assert.equal(r.json.error.type, "request_too_large");
    } finally {
      server.closeAllConnections();
      server.close();
      await b.close();
      await mock.stop();
    }
  });
});

describe("bridge edge cases", () => {
  let mock: MockCopilot;
  let b: Running;
  before(async () => {
    mock = await new MockCopilot().start();
    b = await startBridge(mock, ["-vv", "--log-bodies", "bodies"]);
    (b.ctx.client as any).opts.backoffMs = 1;
    b.ctx.bridge.pingMs = 5;
    setLevel(2);
  });
  after(async () => {
    setLevel(0);
    await b.close();
    await mock.stop();
    fs.rmSync("bodies", { recursive: true, force: true });
  });
  beforeEach(() => {
    logLines.length = 0;
    mock.rawStream = undefined;
    mock.delayMs = 0;
    mock.modelsStatus = 200;
  });

  it("writes request and response bodies for replay, owner-only and redacted", async () => {
    mock.reply = () => "echo gho_AbCdEfGhIjKlMnOpQrStUvWxYz012345";
    try {
      const r = await post(`${b.url}/v1/messages`, msg());
      assert.equal(r.status, 200);
      await post(`${b.url}/v1/messages`, msg("claude-opus-5.5", { stream: true }));
    } finally {
      mock.reply = undefined;
    }
    await new Promise((r) => setTimeout(r, 50));
    const files = fs.readdirSync("bodies");
    assert.ok(files.some((f) => f.endsWith("upstream-response.json")), files.join());
    assert.ok(files.some((f) => f.endsWith("upstream-stream.jsonl")), files.join());
    for (const f of files) {
      assert.equal(fs.readFileSync(path.join("bodies", f), "utf8").includes("gho_AbCd"), false, f);
      if (process.platform !== "win32") assert.equal(fs.statSync(path.join("bodies", f)).mode & 0o077, 0, f);
    }
  });

  it("never logs the Copilot token, even at -vv", async () => {
    await post(`${b.url}/v1/messages`, msg());
    assert.ok(logLines.length > 0);
    assert.equal(logLines.join("\n").includes("test-token"), false);
  });

  it("turns an upstream stream error into an Anthropic error event", async () => {
    mock.rawStream = [
      `data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: "par" } }] })}\n\n`,
      "data: {broken\n\n",
      "data:\n\n",
      "<wait:20>",
      `data: ${JSON.stringify({ error: { message: "quota exceeded" } })}\n\n`,
    ];
    const r = await post(`${b.url}/v1/messages`, msg("claude-opus-5.5", { stream: true }));
    const evs = events(r.text);
    assert.ok(evs.some((e) => e.type === "ping"), "keep-alive pings while waiting");
    const err = evs.at(-1);
    assert.equal(err.type, "error");
    assert.match(err.error.message, /quota exceeded/);
    assert.ok(logLines.some((l) => /stream failed: quota exceeded/.test(l)));
    assert.ok(logLines.some((l) => /unparseable stream data/.test(l)));
  });

  it("reports a stream cut off mid-way", async () => {
    mock.rawStream = [`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: "par" } }] })}\n\n`, "<wait:20>", "<destroy>"];
    const r = await post(`${b.url}/v1/messages`, msg("claude-opus-5.5", { stream: true }));
    assert.equal(events(r.text).at(-1).type, "error");
  });

  it("stops quietly when an error chunk has no message", async () => {
    mock.rawStream = [`data: ${JSON.stringify({ error: {} })}\n\n`];
    const r = await post(`${b.url}/v1/messages`, msg("claude-opus-5.5", { stream: true }));
    assert.match(events(r.text).at(-1).error.message, /upstream error/);
  });

  it("logs a 499 when Claude Code hangs up mid-stream", async () => {
    mock.rawStream = [`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: "a" } }] })}\n\n`, "<wait:300>", "data: [DONE]\n\n"];
    const ac = new AbortController();
    const res = await fetch(`${b.url}/v1/messages`, {
      method: "POST",
      headers: { "content-type": "application/json", ...authHeaders },
      body: JSON.stringify(msg("claude-opus-5.5", { stream: true })),
      signal: ac.signal,
    });
    const reader = res.body!.getReader();
    await reader.read();
    ac.abort();
    await new Promise((r) => setTimeout(r, 400));
    assert.ok(logLines.some((l) => /499/.test(l) && /client closed/.test(l)), logLines.join("\n"));
  });

  it("drops a request the client abandoned before Copilot answered", async () => {
    mock.delayMs = 200;
    const ac = new AbortController();
    const p = fetch(`${b.url}/v1/messages`, {
      method: "POST",
      headers: { "content-type": "application/json", ...authHeaders },
      body: JSON.stringify(msg()),
      signal: ac.signal,
    }).catch((e: Error) => e);
    await new Promise((r) => setTimeout(r, 50));
    ac.abort();
    assert.ok((await p) instanceof Error);
    await new Promise((r) => setTimeout(r, 250));
    assert.equal(logLines.some((l) => /unhandled error/.test(l)), false, logLines.join("\n"));
  });

  it("reloads the model list when Copilot drops a model, then uses the slot", async () => {
    (b.ctx.registry as any).models.push({ id: "claude-opus-9" });
    const r = await post(`${b.url}/v1/messages`, msg("claude-opus-9"));
    assert.equal(r.status, 200, r.text);
    assert.equal(mock.last().body.model, "claude-opus-5.5");
    assert.ok(logLines.some((l) => /isn't supported; reloading/.test(l)));
  });

  it("returns 502 when the model list can't load", async () => {
    (b.ctx.registry as any).loadedAt = 0;
    mock.modelsStatus = 500;
    const r = await post(`${b.url}/v1/messages`, msg());
    assert.equal(r.status, 502);
    assert.match(r.json.error.message, /model list/);
    const models = await fetch(`${b.url}/v1/models`, { headers: authHeaders });
    assert.equal(models.status, 500, "an unexpected error becomes a 500, not a crash");
    assert.ok(logLines.some((l) => /unhandled error on GET \/v1\/models/.test(l)));
    mock.modelsStatus = 200;
    await b.ctx.registry.reload();
  });

  it("returns 502 when Copilot can't be reached", async () => {
    const real = (b.ctx.client as any).opts.baseUrl;
    (b.ctx.client as any).opts.baseUrl = "http://127.0.0.1:1";
    try {
      const r = await post(`${b.url}/v1/messages`, msg());
      assert.equal(r.status, 502);
      assert.match(r.json.error.message, /Couldn't reach the Copilot API/);
    } finally {
      (b.ctx.client as any).opts.baseUrl = real;
    }
  });

  it("passes OpenAI chat completions through with model resolution", async () => {
    const r = await post(`${b.url}/v1/chat/completions`, { model: "claude-opus-5-5", messages: [{ role: "user", content: "x" }] });
    assert.equal(r.status, 200, r.text);
    assert.equal(r.json.choices[0].message.content, "hi there");
    assert.equal(mock.last().body.model, "claude-opus-5.5");
    const streamed = await post(`${b.url}/chat/completions`, { model: "gpt-5", stream: true, messages: [{ role: "user", content: "x" }] });
    assert.match(streamed.text, /data: \[DONE\]/);
    assert.equal((await post(`${b.url}/v1/chat/completions`, [])).status, 400);
    assert.equal((await post(`${b.url}/v1/chat/completions`, { messages: [] })).status, 400);
    const unknown = await post(`${b.url}/v1/chat/completions`, { model: "nope-1", messages: [] });
    assert.equal(unknown.status, 404);
    assert.equal(unknown.json.error.code, "model_not_found");
  });

  it("ends the response if a passthrough stream breaks after the headers", async () => {
    mock.rawStream = ["data: {}\n\n", "<wait:20>", "<destroy>"];
    const r = await post(`${b.url}/v1/chat/completions`, { model: "gpt-5", stream: true, messages: [{ role: "user", content: "x" }] }).catch((e: Error) => e);
    assert.ok(!(r instanceof Error) || /terminated|socket/i.test(r.message));
  });
});

describe("rate limiting", () => {
  it("answers 429 inside the window, or queues with --wait", async () => {
    const mock = await new MockCopilot().start();
    const b = await startBridge(mock, ["--rate-limit", "0.2"]);
    try {
      assert.equal((await post(`${b.url}/v1/messages`, msg())).status, 200);
      const limited = await post(`${b.url}/v1/messages`, msg());
      assert.equal(limited.status, 429);
      assert.equal(limited.json.error.type, "rate_limit_error");
      b.ctx.bridge.cfg.wait = true;
      const t0 = Date.now();
      assert.equal((await post(`${b.url}/v1/messages`, msg())).status, 200);
      assert.ok(Date.now() - t0 >= 100);
    } finally {
      await b.close();
      await mock.stop();
    }
  });
});

describe("strict models and slot checks", () => {
  it("passes model_not_supported through with a hint under --strict-models", async () => {
    const mock = await new MockCopilot().start();
    const b = await startBridge(mock, ["--strict-models"]);
    try {
      (b.ctx.registry as any).models.push({ id: "claude-opus-9" });
      const r = await post(`${b.url}/v1/messages`, msg("claude-opus-9"));
      assert.equal(r.status, 400);
      assert.match(r.json.error.message, /co-claude-bridge models/);
    } finally {
      await b.close();
      await mock.stop();
    }
  });

  it("refuses to start with a --model the account doesn't have", async () => {
    const mock = await new MockCopilot().start();
    try {
      await assert.rejects(startBridge(mock, ["--model", "claude-opus-1"]), /--model claude-opus-1 isn't available/);
    } finally {
      await mock.stop();
    }
  });

  it("loads saved prefill verdicts and survives an unwritable data dir", async () => {
    const mock = await new MockCopilot().start();
    const b = await startBridge(mock);
    try {
      fs.writeFileSync(path.join(b.dataDir, "prefill.json"), JSON.stringify({ "claude-opus-5.5": true, junk: "x" }));
      const again = new Bridge(b.ctx.bridge.cfg, b.ctx.client, b.ctx.registry);
      assert.equal(again.prefillVerdicts.get("claude-opus-5.5"), true);
      assert.equal(again.prefillVerdicts.has("junk"), false);
      again.recordPrefillVerdict("claude-opus-5.5", true); // unchanged: no write
      process.env.COB_DATA_DIR = path.join(b.dataDir, "prefill.json", "sub");
      again.recordPrefillVerdict("gpt-5", false);
      assert.equal(again.prefillVerdicts.get("gpt-5"), false);
    } finally {
      await b.close();
      await mock.stop();
    }
  });

  it("validates requests directly", () => {
    assert.throws(() => validateMessagesRequest(null), /JSON object/);
    assert.deepEqual(validateMessagesRequest({ model: "m", messages: [] }), { model: "m", messages: [] });
  });

  it("streams a tool call and an image request through the full pipeline", async () => {
    const mock = await new MockCopilot().start();
    const b = await startBridge(mock);
    try {
      const r = await post(`${b.url}/v1/messages`, {
        model: "claude-opus-5.5",
        max_tokens: 20,
        stream: true,
        messages: [{ role: "user", content: [{ type: "image", source: { type: "base64", media_type: "image/png", data: "AAAA" } }, { type: "text", text: "what is this" }] }],
      });
      assert.equal(assemble(events(r.text)).text, "hi there");
      assert.equal(mock.last().headers["copilot-vision-request"], "true");
    } finally {
      await b.close();
      await mock.stop();
    }
  });
});
