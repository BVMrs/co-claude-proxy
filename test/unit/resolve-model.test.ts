import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { normalizations, parseClaudeId, pickSlots, resolveModel } from "../../src/pipeline/resolve-model.ts";

const IDS = ["claude-opus-5.5", "claude-opus-4.8", "claude-sonnet-5.5", "claude-sonnet-4.5", "claude-haiku-4.5", "claude-haiku-5.5", "gpt-5"];

describe("resolveModel", () => {
  it("keeps exact IDs", () => {
    assert.deepEqual(resolveModel("gpt-5", IDS), { ok: true, id: "gpt-5", how: "exact" });
  });
  it("normalizes Claude Code's dashed, dated and [1m] IDs", () => {
    for (const [asked, want] of [
      ["claude-sonnet-4-5-20250929", "claude-sonnet-4.5"],
      ["claude-opus-4-8", "claude-opus-4.8"],
      ["claude-opus-5-5[1m]", "claude-opus-5.5"],
      ["anthropic/claude-haiku-4-5", "claude-haiku-4.5"],
      ["Claude-Opus-5.5", "claude-opus-5.5"],
    ]) {
      const r = resolveModel(asked!, IDS);
      assert.deepEqual(r, { ok: true, id: want, how: "normalized" }, asked);
    }
  });
  it("uses aliases, and rejects an alias to a missing model", () => {
    assert.deepEqual(resolveModel("fast", IDS, { aliases: { fast: "claude-haiku-4.5" } }), { ok: true, id: "claude-haiku-4.5", how: "alias" });
    const bad = resolveModel("fast", IDS, { aliases: { fast: "claude-nope" } });
    assert.equal(bad.ok, false);
  });
  it("falls back to the family slot unless strict", () => {
    const opts = { mainSlot: "claude-opus-5.5", sonnetSlot: "claude-sonnet-5.5", smallSlot: "claude-haiku-4.5" };
    assert.deepEqual(resolveModel("claude-3-5-haiku-20241022", IDS, opts), { ok: true, id: "claude-haiku-4.5", how: "slot" });
    assert.deepEqual(resolveModel("claude-sonnet-4-0", IDS, opts), { ok: true, id: "claude-sonnet-5.5", how: "slot" });
    assert.equal(resolveModel("claude-sonnet-4-0", IDS, { ...opts, strict: true }).ok, false);
    assert.equal(resolveModel("gemini-9", IDS, opts).ok, false);
  });
  it("lists valid IDs in the error", () => {
    const r = resolveModel("nope", IDS);
    assert.ok(!r.ok && r.message.includes("claude-haiku-4.5, claude-haiku-5.5"));
  });
});

describe("helpers", () => {
  it("parses both Claude ID styles", () => {
    assert.deepEqual(parseClaudeId("claude-opus-4.8"), { family: "opus", version: [4, 8] });
    assert.deepEqual(parseClaudeId("claude-sonnet-4-5-20250929"), { family: "sonnet", version: [4, 5] });
    assert.deepEqual(parseClaudeId("claude-sonnet-4-20250514"), { family: "sonnet", version: [4] });
    assert.deepEqual(parseClaudeId("claude-3.5-sonnet"), { family: "sonnet", version: [3, 5] });
    assert.equal(parseClaudeId("gpt-5"), undefined);
  });
  it("normalizations are ordered most specific first", () => {
    assert.deepEqual(normalizations("claude-opus-4-8-20260101[1m]").slice(0, 2), ["claude-opus-4-8-20260101", "claude-opus-4-8"]);
  });
  it("picks the newest opus as main and haiku 4.5 for helpers", () => {
    assert.deepEqual(pickSlots(IDS), { main: "claude-opus-5.5", opus: "claude-opus-5.5", sonnet: "claude-sonnet-5.5", small: "claude-haiku-4.5" });
    assert.equal(pickSlots(["claude-haiku-5.5", "claude-sonnet-5.5"]).small, "claude-haiku-5.5");
    assert.equal(pickSlots(IDS, { main: "claude-sonnet-5.5" }).main, "claude-sonnet-5.5");
  });
});
