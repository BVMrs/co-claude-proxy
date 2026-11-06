import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { PrefixStripper } from "../../src/pipeline/prefix-strip.ts";
import { prefillInstruction, rejectsPrefill, repairRequest } from "../../src/pipeline/repair.ts";
import type { MessagesRequest } from "../../src/types/anthropic.ts";

const req = (messages: MessagesRequest["messages"], extra: Partial<MessagesRequest> = {}): MessagesRequest => ({
  model: "claude-opus-5.5",
  max_tokens: 10,
  messages,
  ...extra,
});
const auto = { mode: "auto" as const, modelRejectsPrefill: true };

describe("rejectsPrefill", () => {
  it("guesses from the version, and learned verdicts win", () => {
    const none = new Map<string, boolean>();
    assert.equal(rejectsPrefill("claude-opus-5.5", none), true);
    assert.equal(rejectsPrefill("claude-sonnet-4.6", none), true);
    assert.equal(rejectsPrefill("claude-haiku-4.5", none), false);
    assert.equal(rejectsPrefill("gemini-3.6-flash", none), true);
    assert.equal(rejectsPrefill("gpt-5", none), false);
    assert.equal(rejectsPrefill("claude-opus-5.5", new Map([["claude-opus-5.5", false]])), false);
  });
});

describe("repairRequest", () => {
  it("folds trailing and inline system messages into system, keeping cache_control", () => {
    const env = { type: "text" as const, text: "env", cache_control: { type: "ephemeral" as const } };
    const input = req([{ role: "user", content: "hi" }, { role: "system", content: [env] }], { system: "base" });
    const frozen = JSON.stringify(input);
    const r = repairRequest(input, auto);
    assert.equal(JSON.stringify(input), frozen, "input not mutated");
    assert.equal(r.systemFolded, 1);
    assert.equal(r.lastRole, "system");
    assert.equal(r.prefill, "none");
    assert.deepEqual(r.request.system, [{ type: "text", text: "base" }, env]);
    assert.deepEqual(r.request.messages, [{ role: "user", content: "hi" }]);
  });

  it("converts a text prefill into an instruction on the last user turn", () => {
    const r = repairRequest(req([{ role: "user", content: "q" }, { role: "assistant", content: "{" }]), auto);
    assert.equal(r.prefill, "converted");
    assert.equal(r.prefillText, "{");
    assert.deepEqual(r.request.messages, [
      { role: "user", content: [{ type: "text", text: "q" }, { type: "text", text: prefillInstruction("{") }] },
    ]);
  });

  it("keeps the prefill for models that accept it, and with --prefill off", () => {
    const m = req([{ role: "user", content: "q" }, { role: "assistant", content: "{" }]);
    assert.equal(repairRequest(m, { mode: "auto", modelRejectsPrefill: false }).prefill, "kept");
    assert.equal(repairRequest(m, { mode: "off", modelRejectsPrefill: true }).prefill, "kept");
  });

  it("--prefill drop removes the turn; convert applies even to accepting models", () => {
    const m = req([{ role: "user", content: "q" }, { role: "assistant", content: "{" }]);
    const d = repairRequest(m, { mode: "drop", modelRejectsPrefill: false });
    assert.equal(d.prefill, "dropped");
    assert.deepEqual(d.request.messages, [{ role: "user", content: "q" }]);
    assert.equal(repairRequest(m, { mode: "convert", modelRejectsPrefill: false }).prefill, "converted");
  });

  it("always drops empty and dangling tool_use trailing turns", () => {
    const empty = repairRequest(req([{ role: "user", content: "q" }, { role: "assistant", content: [] }]), { mode: "off", modelRejectsPrefill: false });
    assert.equal(empty.prefill, "dropped-empty");
    const tool = repairRequest(
      req([{ role: "user", content: "q" }, { role: "assistant", content: [{ type: "tool_use", id: "t", name: "x", input: {} }] }]),
      auto,
    );
    assert.equal(tool.prefill, "dropped-tool-use");
    assert.equal(tool.request.messages.length, 1);
  });

  it("merges a run of trailing assistant turns and ignores thinking", () => {
    const r = repairRequest(
      req([
        { role: "user", content: "q" },
        { role: "assistant", content: [{ type: "thinking", thinking: "hmm" }, { type: "text", text: "A" }] },
        { role: "assistant", content: "B" },
      ]),
      auto,
    );
    assert.equal(r.prefillText, "AB");
  });

  it("adds a user turn when the conversation before the prefill ends on a tool result", () => {
    const r = repairRequest(
      req([
        { role: "user", content: [{ type: "tool_result", tool_use_id: "t", content: "ok" }] },
        { role: "assistant", content: "Done:" },
      ]),
      auto,
    );
    const last = r.request.messages.at(-1)!;
    assert.equal(last.role, "user");
    assert.deepEqual((last.content as any[]).map((b) => b.type), ["tool_result", "text"]);
  });
});

describe("PrefixStripper", () => {
  const run = (prefix: string, pieces: string[]) => {
    const s = new PrefixStripper(prefix);
    return pieces.map((p) => s.push(p)).join("") + s.flush();
  };
  it("strips an echoed prefix split across chunks", () => {
    assert.equal(run('{"a":', ['{"', 'a"', ':1}']), "1}");
  });
  it("tolerates leading whitespace before the echo", () => {
    assert.equal(run("Hello", ["\n Hel", "lo world"]), " world");
  });
  it("passes text through when the model doesn't echo", () => {
    assert.equal(run("Hello", ["He", "y there"]), "Hey there");
  });
  it("returns nothing when the reply is only part of the prefix", () => {
    assert.equal(run("Hello world", ["Hello"]), "");
  });
});
