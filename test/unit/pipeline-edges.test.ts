// Edge cases of the translation pipeline that the main suites don't reach.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { configDir, dataDir } from "../../src/paths.ts";
import { estimateTokens } from "../../src/pipeline/count-tokens.ts";
import { errorBody, errorType, isModelNotSupported, isPrefillError, upstreamMessage } from "../../src/pipeline/errors.ts";
import { fromOpenAI } from "../../src/pipeline/from-openai.ts";
import { repairRequest } from "../../src/pipeline/repair.ts";
import { toOpenAI } from "../../src/pipeline/to-openai.ts";
import { getLevel, setLevel } from "../../src/log.ts";
import type { MessagesRequest } from "../../src/types/anthropic.ts";

describe("errors", () => {
  it("maps statuses to Anthropic error types", () => {
    const want: Record<number, string> = {
      400: "invalid_request_error",
      422: "invalid_request_error",
      401: "authentication_error",
      403: "permission_error",
      404: "not_found_error",
      413: "request_too_large",
      429: "rate_limit_error",
      503: "overloaded_error",
      529: "overloaded_error",
      500: "api_error",
    };
    for (const [s, t] of Object.entries(want)) assert.equal(errorType(Number(s)), t, s);
    assert.deepEqual(errorBody(404, "x"), { type: "error", error: { type: "not_found_error", message: "x" } });
  });
  it("pulls a message out of any upstream error shape", () => {
    assert.deepEqual(upstreamMessage('{"error":"flat"}'), { message: "flat" });
    assert.deepEqual(upstreamMessage('{"error":{"message":"m","code":"c"}}'), { message: "m", code: "c" });
    assert.deepEqual(upstreamMessage('{"error":{"type":"t"}}'), { message: '{"error":{"type":"t"}}', code: "t" });
    assert.deepEqual(upstreamMessage('{"message":"top"}'), { message: "top", code: undefined });
    assert.deepEqual(upstreamMessage("  plain text  "), { message: "plain text" });
    assert.deepEqual(upstreamMessage(""), { message: "(empty response body)" });
  });
  it("recognises prefill and unsupported-model errors", () => {
    assert.ok(isPrefillError("This model does not support assistant message prefill."));
    assert.ok(isPrefillError("The last message in the conversation must not be from the assistant"));
    assert.ok(!isPrefillError("rate limited"));
    assert.ok(isModelNotSupported('{"code":"model_not_supported"}'));
  });
});

describe("estimateTokens", () => {
  it("counts images as a fixed cost and skips cache_control", () => {
    const base: MessagesRequest = { model: "m", max_tokens: 1, messages: [{ role: "user", content: "hi" }] };
    const withImage: MessagesRequest = {
      ...base,
      messages: [{ role: "user", content: [{ type: "image", source: { type: "base64", media_type: "image/png", data: "A".repeat(100000) } }] }],
    };
    const n = estimateTokens(withImage);
    assert.ok(n >= 1600 && n < 1610, `image counted as ~1.6k tokens regardless of size, got ${n}`);
    const cached: MessagesRequest = { ...base, system: [{ type: "text", text: "hi", cache_control: { type: "ephemeral" } }] };
    assert.equal(estimateTokens(cached), estimateTokens({ ...base, system: [{ type: "text", text: "hi" }] }));
    assert.equal(estimateTokens({ ...base, messages: [] }), 1, "never zero");
    assert.ok(estimateTokens({ ...base, tools: [{ name: "t", input_schema: { type: "object" } }] }) > 346);
  });
});

describe("fromOpenAI stop reasons", () => {
  const reply = (finish_reason: string | null) =>
    fromOpenAI({ id: "x", model: "m", choices: [{ index: 0, message: { role: "assistant", content: "ok" }, finish_reason }] } as never, "m");
  it("maps every finish reason", () => {
    assert.equal(reply("function_call").stop_reason, "tool_use");
    assert.equal(reply("content_filter").stop_reason, "refusal");
    assert.equal(reply(null).stop_reason, "end_turn");
    assert.equal(reply("length").stop_reason, "max_tokens");
  });
});

describe("toOpenAI tool choice", () => {
  const req = (tool_choice: unknown): MessagesRequest =>
    ({ model: "m", max_tokens: 1, messages: [{ role: "user", content: "x" }], tools: [{ name: "t", input_schema: {} }], tool_choice }) as never;
  it("maps auto, none, a named tool and nothing", () => {
    assert.equal(toOpenAI(req({ type: "auto" }), "m").payload.tool_choice, "auto");
    assert.equal(toOpenAI(req({ type: "none" }), "m").payload.tool_choice, "none");
    assert.deepEqual(toOpenAI(req({ type: "tool", name: "t" }), "m").payload.tool_choice, { type: "function", function: { name: "t" } });
    assert.equal(toOpenAI(req(undefined), "m").payload.tool_choice, undefined);
  });
});

describe("repairRequest corner cases", () => {
  it("keeps a conversation made only of assistant turns", () => {
    const r = repairRequest({ model: "m", max_tokens: 1, messages: [{ role: "assistant", content: "a" }] }, { mode: "convert", modelRejectsPrefill: true });
    assert.equal(r.prefill, "kept");
  });
});

describe("paths", () => {
  it("follow XDG on Unix and APPDATA on Windows", () => {
    assert.equal(dataDir({ COB_DATA_DIR: "/d" }), "/d");
    assert.equal(dataDir({ XDG_DATA_HOME: "/x" }, "linux"), "/x/co-claude-bridge");
    assert.match(dataDir({}, "linux"), /\.local[/\\]share[/\\]co-claude-bridge$/);
    assert.match(dataDir({ APPDATA: "C:\\A" }, "win32"), /co-claude-bridge$/);
    assert.match(dataDir({}, "win32"), /AppData[/\\]Roaming[/\\]co-claude-bridge$/);
    assert.equal(configDir({ COB_CONFIG_DIR: "/c" }), "/c");
    assert.equal(configDir({ APPDATA: "/a" }, "win32"), dataDir({ APPDATA: "/a" }, "win32"));
    assert.equal(configDir({ XDG_CONFIG_HOME: "/y" }, "linux"), "/y/co-claude-bridge");
    assert.match(configDir({}, "linux"), /\.config[/\\]co-claude-bridge$/);
  });
});

describe("log level", () => {
  it("round-trips", () => {
    const before = getLevel();
    setLevel(2);
    assert.equal(getLevel(), 2);
    setLevel(before);
  });
});
