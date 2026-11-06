import assert from "node:assert/strict";
import http from "node:http";
import type { AddressInfo } from "node:net";
import { after, before, beforeEach, describe, it } from "node:test";

import { CopilotAuth, staticTokenFetcher } from "../../src/auth/copilot-token.ts";
import { CopilotClient, UpstreamError } from "../../src/copilot/client.ts";
import { ModelRegistry } from "../../src/copilot/models.ts";
import { readSse } from "../../src/copilot/sse.ts";
import { UnsafeUrlError } from "../../src/security/upstream.ts";
import { logLines } from "../helpers/bridge.ts";

type Handler = (req: http.IncomingMessage, res: http.ServerResponse) => void;
let handler: Handler;
let hits = 0;
const server = http.createServer((req, res) => {
  hits++;
  handler(req, res);
});
let url = "";
before(async () => {
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  url = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
after(() => {
  server.closeAllConnections();
  server.close();
});
beforeEach(() => {
  hits = 0;
  logLines.length = 0;
});

const auth = (endpoint?: string) => {
  const a = new CopilotAuth(async () => ({ token: "t", expires_at: Date.now() / 1000 + 3600, endpoints: endpoint ? { api: endpoint } : undefined }));
  return a;
};
const client = (opts: Partial<ConstructorParameters<typeof CopilotClient>[0]> = {}) =>
  new CopilotClient({ auth: auth(), baseUrl: url, defaultBaseUrl: "https://api.githubcopilot.com", backoffMs: 1, ...opts });

describe("CopilotClient base URL", () => {
  it("refuses a plain-http base URL to another machine", () => {
    assert.throws(() => client({ baseUrl: "http://copilot.example.com" }), UnsafeUrlError);
  });

  it("uses a trusted announced endpoint and ignores an untrusted one, warning once", async () => {
    const good = auth("https://api.business.githubcopilot.com/");
    await good.refresh();
    assert.equal(new CopilotClient({ auth: good, defaultBaseUrl: "https://d.githubcopilot.com" }).baseUrl(), "https://api.business.githubcopilot.com");

    const bad = auth("https://evil.example");
    await bad.refresh();
    const c = new CopilotClient({ auth: bad, defaultBaseUrl: "https://api.githubcopilot.com/" });
    assert.equal(c.baseUrl(), "https://api.githubcopilot.com");
    assert.equal(c.baseUrl(), "https://api.githubcopilot.com");
    assert.equal(logLines.filter((l) => /Ignoring the Copilot API endpoint https:\/\/evil\.example/.test(l)).length, 1);

    const none = new CopilotClient({ auth: auth(), defaultBaseUrl: "https://api.githubcopilot.com" });
    assert.equal(none.baseUrl(), "https://api.githubcopilot.com");
  });
});

describe("CopilotClient retries", () => {
  it("retries 5xx and 429, honouring a short Retry-After", async () => {
    const statuses = [503, 429, 200];
    handler = (_req, res) => {
      const s = statuses.shift()!;
      res.writeHead(s, s === 429 ? { "retry-after": "0.001" } : {});
      res.end(s === 200 ? JSON.stringify({ data: [{ id: "m" }] }) : "busy");
    };
    const r = await client().models();
    assert.deepEqual(r.data.map((m) => m.id), ["m"]);
    assert.equal(hits, 3);
  });

  it("gives up after maxRetries and reports the status", async () => {
    handler = (_req, res) => res.writeHead(500).end("still down");
    await assert.rejects(client({ maxRetries: 1 }).models(), (e: unknown) => e instanceof UpstreamError && e.status === 500 && /still down/.test(e.message));
    assert.equal(hits, 2);
  });

  it("retries a network failure, then surfaces it", async () => {
    const c = client({ baseUrl: "http://127.0.0.1:1", maxRetries: 1 });
    await assert.rejects(c.models());
  });

  it("does not retry once the caller aborted", async () => {
    const ac = new AbortController();
    ac.abort();
    await assert.rejects(client({ maxRetries: 3 }).chat({ model: "m", messages: [] }, { signal: ac.signal }));
    assert.equal(hits, 0);
  });

  it("refreshes the token once on 401, then returns the second 401", async () => {
    let refreshes = 0;
    const a = new CopilotAuth(async () => {
      refreshes++;
      return { token: `t${refreshes}`, expires_at: Date.now() / 1000 + 3600 };
    });
    handler = (_req, res) => res.writeHead(401).end("{}");
    const res = await new CopilotClient({ auth: a, baseUrl: url, defaultBaseUrl: url, backoffMs: 1 }).chat({ model: "m", messages: [] });
    assert.equal(res.status, 401);
    assert.equal(refreshes, 2, "initial fetch + one forced refresh");
    assert.equal(hits, 2);
  });

  it("never follows a redirect with the Copilot token", async () => {
    let leaked = false;
    const other = http.createServer((_req, res) => {
      leaked = true;
      res.end("{}");
    });
    await new Promise<void>((r) => other.listen(0, "127.0.0.1", () => r()));
    handler = (_req, res) => res.writeHead(307, { location: `http://127.0.0.1:${(other.address() as AddressInfo).port}/models` }).end();
    try {
      await assert.rejects(client({ maxRetries: 0 }).models());
      assert.equal(leaked, false);
    } finally {
      other.close();
    }
  });
});

describe("ModelRegistry", () => {
  it("keeps chat models once, caches them, and shares a reload", async () => {
    let calls = 0;
    const reg = new ModelRegistry({
      models: async () => {
        calls++;
        return { data: [{ id: "a" }, { id: "a" }, { id: "e", capabilities: { type: "embeddings" } }, { id: "b", capabilities: { type: "chat" } }] };
      },
    });
    assert.deepEqual(reg.cached(), []);
    await Promise.all([reg.list(), reg.list()]);
    await reg.list();
    assert.equal(calls, 1);
    assert.deepEqual(reg.ids(), ["a", "b"]);
    assert.equal(reg.get("b")?.id, "b");
    assert.equal(reg.get("e"), undefined);
    await reg.reload();
    assert.equal(calls, 2);
  });
  it("copes with a response without data", async () => {
    const reg = new ModelRegistry({ models: async () => ({}) as never });
    assert.deepEqual(await reg.list(), []);
  });
});

describe("readSse", () => {
  const body = (...parts: string[]) =>
    new ReadableStream<Uint8Array>({
      start(c) {
        for (const p of parts) c.enqueue(new TextEncoder().encode(p));
        c.close();
      },
    });
  const all = async (s: ReadableStream<Uint8Array>) => {
    const out = [];
    for await (const e of readSse(s)) out.push(e);
    return out;
  };
  it("splits events across chunk boundaries and both line endings", async () => {
    const evs = await all(body("data: a\n", "\ndata: b\r\n\r\n: comment\n\nevent: x\ndata:c\ndata: d\n\n", "data: tail"));
    assert.deepEqual(evs, [{ event: undefined, data: "a" }, { event: undefined, data: "b" }, { event: "x", data: "c\nd" }, { event: undefined, data: "tail" }]);
  });
  it("handles fields without a colon and event-only blocks", async () => {
    assert.deepEqual(await all(body("data\n\nevent: ping\n\n")), [{ event: undefined, data: "" }, { event: "ping", data: "" }]);
  });
  it("prefers the earlier boundary when both styles appear", async () => {
    assert.deepEqual(await all(body("data: a\r\n\r\ndata: b\n\n")), [{ event: undefined, data: "a" }, { event: undefined, data: "b" }]);
  });
});

describe("staticTokenFetcher in a client", () => {
  it("sends the fixed token as a bearer", async () => {
    let seen = "";
    handler = (req, res) => {
      seen = String(req.headers.authorization);
      res.end(JSON.stringify({ data: [] }));
    };
    await new CopilotClient({ auth: new CopilotAuth(staticTokenFetcher("fixed")), baseUrl: url, defaultBaseUrl: url }).models();
    assert.equal(seen, "Bearer fixed");
  });
});
