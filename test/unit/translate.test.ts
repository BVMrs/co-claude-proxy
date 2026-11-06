import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { fromOpenAI } from "../../src/pipeline/from-openai.ts";
import { StreamTranslator } from "../../src/pipeline/stream.ts";
import { initiatorOf, toOpenAI } from "../../src/pipeline/to-openai.ts";
import type { MessagesRequest } from "../../src/types/anthropic.ts";

describe("toOpenAI", () => {
  it("translates system, images, tool results with images, and tool settings", () => {
    const req: MessagesRequest = {
      model: "x",
      max_tokens: 100,
      stream: true,
      system: [{ type: "text", text: "A" }, { type: "text", text: "B" }],
      stop_sequences: ["END"],
      tools: [
        { name: "Read", input_schema: { type: "object" } },
        { name: "web_search", type: "web_search_20250305" },
      ],
      tool_choice: { type: "any", disable_parallel_tool_use: true },
      messages: [
        { role: "user", content: [{ type: "text", text: "look" }, { type: "image", source: { type: "base64", media_type: "image/png", data: "AAA" } }] },
        { role: "assistant", content: [{ type: "tool_use", id: "t1", name: "Read", input: { p: 1 } }] },
        {
          role: "user",
          content: [
            { type: "tool_result", tool_use_id: "t1", is_error: true, content: [{ type: "text", text: "nope" }, { type: "image", source: { type: "url", url: "http://x/i.png" } }] },
            { type: "text", text: "next" },
          ],
        },
      ],
    };
    const { payload, vision, droppedTools } = toOpenAI(req, "claude-opus-5.5");
    assert.equal(payload.model, "claude-opus-5.5");
    assert.deepEqual(payload.stream_options, { include_usage: true });
    assert.deepEqual(payload.messages[0], { role: "system", content: "A\n\nB" });
    assert.equal(vision, true);
    assert.deepEqual(droppedTools, ["web_search"]);
    assert.equal(payload.tools!.length, 1);
    assert.equal(payload.tool_choice, "required");
    assert.equal(payload.parallel_tool_calls, false);
    assert.deepEqual(payload.stop, ["END"]);
    const tool = payload.messages[3] as any;
    assert.equal(tool.role, "tool");
    assert.match(tool.content, /^Error: nope/);
    const after = payload.messages[4] as any;
    assert.equal(after.role, "user");
    assert.ok(after.content.some((p: any) => p.type === "image_url" && p.image_url.url === "http://x/i.png"));
    assert.ok(after.content.some((p: any) => p.type === "text" && p.text === "next"));
  });

  it("marks tool-result-only turns as agent-initiated", () => {
    const base = { model: "x", max_tokens: 1 };
    assert.equal(initiatorOf({ ...base, messages: [{ role: "user", content: "hi" }] }), "user");
    assert.equal(
      initiatorOf({
        ...base,
        messages: [{ role: "user", content: [{ type: "tool_result", tool_use_id: "t" }, { type: "text", text: "<system-reminder>x</system-reminder>" }] }],
      }),
      "agent",
    );
    assert.equal(
      initiatorOf({ ...base, messages: [{ role: "user", content: [{ type: "tool_result", tool_use_id: "t" }, { type: "text", text: "and also" }] }] }),
      "user",
    );
  });
});

describe("fromOpenAI", () => {
  it("merges choices, parses arguments and maps usage", () => {
    const out = fromOpenAI(
      {
        id: "chatcmpl-9",
        choices: [
          { index: 0, message: { role: "assistant", content: "hi" }, finish_reason: "stop" },
          { index: 1, message: { role: "assistant", content: null, tool_calls: [{ id: "c", type: "function", function: { name: "f", arguments: "not json" } }] }, finish_reason: "tool_calls" },
        ],
        usage: { prompt_tokens: 50, completion_tokens: 5, prompt_tokens_details: { cached_tokens: 10 } },
      },
      "claude-x",
    );
    assert.equal(out.id, "msg_chatcmpl9");
    assert.equal(out.model, "claude-x");
    assert.equal(out.stop_reason, "tool_use");
    assert.deepEqual(out.content[1], { type: "tool_use", id: "c", name: "f", input: { _raw_arguments: "not json" } });
    assert.deepEqual(out.usage, { input_tokens: 40, output_tokens: 5, cache_read_input_tokens: 10 });
  });

  it("maps length to max_tokens", () => {
    const out = fromOpenAI({ id: "a", choices: [{ index: 0, message: { role: "assistant", content: "x" }, finish_reason: "length" }] }, "m");
    assert.equal(out.stop_reason, "max_tokens");
  });
});

describe("StreamTranslator", () => {
  it("orders blocks, closes them, and reports late usage", () => {
    const t = new StreamTranslator("m");
    const ev = [
      ...t.chunk({ id: "c1", choices: [{ index: 0, delta: { content: "a" } }] }),
      ...t.chunk({ choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: "t1", function: { name: "f", arguments: "{" } }] } }] }),
      ...t.chunk({ choices: [{ index: 0, delta: { tool_calls: [{ index: 1, id: "t2", function: { name: "g", arguments: "{}" } }] } }] }),
      ...t.chunk({ choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: "}" } }] } }] }),
      ...t.chunk({ choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }] }),
      ...t.chunk({ choices: [], usage: { prompt_tokens: 9, completion_tokens: 3 } }),
      ...t.end(),
    ];
    assert.deepEqual(
      ev.map((e) => e.type + ("index" in e ? e.index : "")),
      [
        "message_start",
        "content_block_start0",
        "content_block_delta0",
        "content_block_stop0",
        "content_block_start1",
        "content_block_delta1",
        "content_block_stop1",
        "content_block_start2",
        "content_block_delta2",
        "content_block_delta1",
        "content_block_stop2",
        "message_delta",
        "message_stop",
      ],
    );
    const md = ev.find((e) => e.type === "message_delta") as any;
    assert.equal(md.delta.stop_reason, "tool_use");
    assert.deepEqual(md.usage, { input_tokens: 9, output_tokens: 3 });
  });

  it("produces a complete message even for an empty stream", () => {
    const t = new StreamTranslator("m");
    assert.deepEqual(t.end().map((e) => e.type), ["message_start", "message_delta", "message_stop"]);
  });
});
