import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { after, before, beforeEach, describe, it } from "node:test";

import { assemble, authHeaders, events, logLines, post, startBridge, type Running } from "../helpers/bridge.ts";
import { MockCopilot, PREFILL_ERROR } from "../helpers/mock-copilot.ts";

// Shape of `claude -p hello` from Claude Code 2.1.318, text shortened.
const HELLO = {
  model: "claude-opus-5.5",
  max_tokens: 32000,
  stream: true,
  system: [{ type: "text", text: "You are Claude Code." }],
  thinking: { type: "adaptive", display: "omitted" },
  context_management: { edits: [{ type: "clear_thinking_20251015", keep: "all" }] },
  output_config: { effort: "medium" },
  messages: [
    { role: "user", content: [{ type: "text", text: "<system-reminder>\nctx\n</system-reminder>" }, { type: "text", text: "hello" }] },
    { role: "system", content: [{ type: "text", text: "# Environment\n - Platform: linux", cache_control: { type: "ephemeral", ttl: "1h" } }] },
  ],
};

describe("bridge against a mock Copilot", () => {
  let mock: MockCopilot;
  let b: Running;
  before(async () => {
    mock = await new MockCopilot().start();
    b = await startBridge(mock, ["-v"]);
  });
  after(async () => {
    await b.close();
    await mock.stop();
  });
  beforeEach(() => {
    logLines.length = 0;
  });

  it("answers Claude Code's plain hello (trailing system message) without a prefill 400", async () => {
    const r = await post(`${b.url}/v1/messages`, HELLO);
    assert.equal(r.status, 200, r.text);
    const msg = assemble(events(r.text));
    assert.equal(msg.text, "hi there");
    assert.equal(msg.stop, "end_turn");
    const sent = mock.last().body;
    assert.deepEqual(sent.messages.map((m) => m.role), ["system", "user"]);
    assert.match(String(sent.messages[0]!.content), /# Environment/);
    assert.equal(mock.last().headers["x-initiator"], "user");
    assert.ok(logLines.some((l) => /system_folded=1/.test(l) && /last=system/.test(l)), logLines.join("\n"));
  });

  it("streams usage that arrives after finish_reason", async () => {
    const r = await post(`${b.url}/v1/messages`, HELLO);
    const delta = events(r.text).find((e) => e.type === "message_delta");
    assert.deepEqual(delta.usage, { input_tokens: 100, output_tokens: 7, cache_read_input_tokens: 20 });
    assert.equal(events(r.text).at(-1).type, "message_stop");
  });

  it("repairs a real prefill for Opus and strips the echoed prefix (non-streaming)", async () => {
    const r = await post(`${b.url}/v1/messages`, {
      model: "claude-opus-5.5",
      max_tokens: 50,
      messages: [
        { role: "user", content: "Give me JSON" },
        { role: "assistant", content: '{"title":' },
      ],
    });
    assert.equal(r.status, 200, r.text);
    assert.equal(r.json.content[0].text, " continued");
    assert.equal(mock.last().body.messages.at(-1)!.role, "user");
  });

  it("repairs a prefill while streaming", async () => {
    const r = await post(`${b.url}/v1/messages`, {
      model: "claude-sonnet-5.5",
      max_tokens: 50,
      stream: true,
      messages: [
        { role: "user", content: [{ type: "text", text: "Name a colour" }] },
        { role: "assistant", content: [{ type: "text", text: "The colour is" }] },
      ],
    });
    assert.equal(assemble(events(r.text)).text, " continued");
  });

  it("keeps the prefill for models that accept it", async () => {
    const r = await post(`${b.url}/v1/messages`, {
      model: "claude-haiku-4.5",
      max_tokens: 8,
      messages: [
        { role: "user", content: "x" },
        { role: "assistant", content: "y" },
      ],
    });
    assert.equal(r.status, 200);
    assert.equal(mock.last().body.messages.at(-1)!.role, "assistant");
  });

  it("drops an empty trailing assistant turn from an interrupted session", async () => {
    const r = await post(`${b.url}/v1/messages`, {
      model: "claude-opus-5.5",
      max_tokens: 8,
      messages: [
        { role: "user", content: "x" },
        { role: "assistant", content: [{ type: "text", text: "  " }] },
      ],
    });
    assert.equal(r.status, 200);
    assert.deepEqual(mock.last().body.messages.map((m) => m.role), ["user"]);
  });

  it("resolves Claude Code's dashed and dated IDs onto Copilot IDs", async () => {
    for (const [asked, want] of [
      ["claude-haiku-4-5-20251001", "claude-haiku-4.5"],
      ["claude-opus-5-5[1m]", "claude-opus-5.5"],
      ["claude-sonnet-4-5", "claude-sonnet-5.5"], // not on the account: sonnet slot
      ["claude-opus-4-1", "claude-opus-5.5"], // not on the account: opus slot
    ]) {
      const r = await post(`${b.url}/v1/messages`, { model: asked, max_tokens: 8, messages: [{ role: "user", content: "x" }] });
      assert.equal(r.status, 200, `${asked}: ${r.text}`);
      assert.equal(mock.last().body.model, want, asked);
      assert.equal(r.json.model, asked, "reply names the model Claude Code asked for");
    }
  });

  it("returns a clear 404 listing valid IDs for an unknown model", async () => {
    const r = await post(`${b.url}/v1/messages`, { model: "gpt-9", max_tokens: 8, messages: [{ role: "user", content: "x" }] });
    assert.equal(r.status, 404);
    assert.equal(r.json.type, "error");
    assert.equal(r.json.error.type, "not_found_error");
    assert.match(r.json.error.message, /claude-opus-5\.5/);
    assert.doesNotMatch(r.json.error.message, /embedding/);
  });

  it("translates tool calls, streamed and not", async () => {
    const tools = [{ name: "Read", description: "read a file", input_schema: { type: "object", properties: { path: { type: "string" } } } }];
    const req = { model: "claude-opus-5.5", max_tokens: 100, tools, messages: [{ role: "user", content: "please use the tool" }] };
    const plain = await post(`${b.url}/v1/messages`, req);
    assert.equal(plain.json.stop_reason, "tool_use");
    assert.deepEqual(plain.json.content.map((c: any) => c.type), ["text", "tool_use"]);
    assert.deepEqual(plain.json.content[1].input, { path: "README.md" });

    const streamed = assemble(events((await post(`${b.url}/v1/messages`, { ...req, stream: true })).text));
    assert.equal(streamed.stop, "tool_use");
    assert.equal(streamed.text, "Let me look.");
    assert.deepEqual(streamed.tools, [{ name: "Read", input: { path: "README.md" } }]);
  });

  it("sends tool results back as tool messages with X-Initiator agent", async () => {
    await post(`${b.url}/v1/messages`, {
      model: "claude-opus-5.5",
      max_tokens: 100,
      messages: [
        { role: "user", content: "read it" },
        { role: "assistant", content: [{ type: "thinking", thinking: "secret", signature: "s" }, { type: "tool_use", id: "call_1", name: "Read", input: { path: "a" } }] },
        { role: "user", content: [{ type: "tool_result", tool_use_id: "call_1", content: [{ type: "text", text: "file body" }] }] },
      ],
    });
    const sent = mock.last();
    assert.equal(sent.headers["x-initiator"], "agent");
    const roles = sent.body.messages.map((m) => m.role);
    assert.deepEqual(roles, ["user", "assistant", "tool"]);
    const asst = sent.body.messages[1] as any;
    assert.equal(asst.content, null, "thinking is not replayed as text");
    assert.equal(asst.tool_calls[0].function.arguments, '{"path":"a"}');
  });

  it("retries a 500 and succeeds", async () => {
    mock.failNext.set("claude-sonnet-5.5", 1);
    const r = await post(`${b.url}/v1/messages`, { model: "claude-sonnet-5.5", max_tokens: 8, messages: [{ role: "user", content: "x" }] });
    assert.equal(r.status, 200);
  });

  it("estimates tokens and lists models in Anthropic format", async () => {
    const t = await post(`${b.url}/v1/messages/count_tokens`, HELLO);
    assert.ok(t.json.input_tokens > 5);
    const m = (await (await fetch(`${b.url}/v1/models`, { headers: authHeaders })).json()) as any;
    assert.ok(m.data.some((x: any) => x.id === "claude-opus-5.5" && x.type === "model"));
    assert.equal(m.has_more, false);
  });
});

describe("learning which models reject prefill", () => {
  it("records that a model accepts prefill when Copilot takes it", async () => {
    const mock = await new MockCopilot().start();
    const b = await startBridge(mock);
    try {
      const body = { model: "gpt-5", max_tokens: 8, messages: [{ role: "user", content: "x" }, { role: "assistant", content: "y" }] };
      assert.equal((await post(`${b.url}/v1/messages`, body)).status, 200);
      assert.equal(mock.last().body.messages.at(-1)!.role, "assistant");
      const saved = JSON.parse(fs.readFileSync(path.join(b.dataDir, "prefill.json"), "utf8"));
      assert.equal(saved["gpt-5"], false);
    } finally {
      await b.close();
      await mock.stop();
    }
  });

  it("learns a rejection from Copilot's 400", async () => {
    const mock = await new MockCopilot().start();
    const b = await startBridge(mock, ["--alias", "my-model=claude-opus-5.5"]);
    try {
      // Pretend we believed Opus accepts prefill.
      b.ctx.bridge.prefillVerdicts.set("claude-opus-5.5", false);
      const r = await post(`${b.url}/v1/messages`, {
        model: "my-model",
        max_tokens: 8,
        messages: [{ role: "user", content: "x" }, { role: "assistant", content: "The answer is" }],
      });
      assert.equal(r.status, 200, r.text);
      assert.equal(r.json.content[0].text, " continued");
      assert.equal(b.ctx.bridge.prefillVerdicts.get("claude-opus-5.5"), true);
      assert.equal(mock.received.filter((x) => x.body.model === "claude-opus-5.5").length, 2);
    } finally {
      await b.close();
      await mock.stop();
    }
  });

  it("passes the prefill 400 through with a hint when repair is off", async () => {
    const mock = await new MockCopilot().start();
    const b = await startBridge(mock, ["--prefill", "off"]);
    try {
      const r = await post(`${b.url}/v1/messages`, {
        model: "claude-opus-5.5",
        max_tokens: 8,
        messages: [{ role: "user", content: "x" }, { role: "assistant", content: "y" }],
      });
      assert.equal(r.status, 400);
      assert.ok(r.json.error.message.startsWith(PREFILL_ERROR));
      assert.match(r.json.error.message, /--prefill convert/);
    } finally {
      await b.close();
      await mock.stop();
    }
  });
});

describe("token refresh", () => {
  it("refreshes the Copilot token once on a 401 and retries", async () => {
    const mock = await new MockCopilot().start();
    const b = await startBridge(mock);
    try {
      let n = 0;
      // Swap in a fetcher that hands out a new token each time.
      (b.ctx.auth as any).fetcher = async () => ({ token: `tok-${++n}`, expires_at: Date.now() / 1000 + 3600 });
      await b.ctx.auth.refresh(); // tok-1, which the mock doesn't accept yet
      mock.validTokens = new Set(["tok-2"]);
      const r = await post(`${b.url}/v1/messages`, { model: "claude-opus-5.5", max_tokens: 8, messages: [{ role: "user", content: "x" }] });
      assert.equal(r.status, 200, r.text);
      assert.equal(mock.last().headers.authorization, "Bearer tok-2");
    } finally {
      await b.close();
      await mock.stop();
    }
  });
});
