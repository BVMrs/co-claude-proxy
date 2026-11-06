import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, it } from "node:test";

import { claudeEnv, formatEnv } from "../../src/commands/env.ts";
import { ConfigError, loadConfig } from "../../src/config.ts";

const none = path.join(os.tmpdir(), "cob-missing-config.json");

describe("loadConfig", () => {
  it("has sensible defaults", () => {
    const c = loadConfig([], {}, none);
    assert.equal(c.command, "start");
    assert.equal(c.port, 4141);
    assert.equal(c.host, "127.0.0.1");
    assert.equal(c.prefill, "auto");
  });
  it("applies file < env < flags", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cob-cfg-"));
    const file = path.join(dir, "config.json");
    fs.writeFileSync(file, JSON.stringify({ port: 5000, prefill: "drop", modelAliases: { a: "b" } }));
    const c = loadConfig(["models", "--port=6000", "-vv", "--alias", "x=y"], { COB_PREFILL: "convert" }, file);
    assert.equal(c.command, "models");
    assert.equal(c.port, 6000);
    assert.equal(c.prefill, "convert");
    assert.equal(c.verbose, 2);
    assert.deepEqual(c.modelAliases, { a: "b", x: "y" });
  });
  it("rejects bad input with a readable message", () => {
    assert.throws(() => loadConfig(["--prefill", "maybe"], {}, none), ConfigError);
    assert.throws(() => loadConfig(["--nope"], {}, none), /unknown flag --nope/);
    assert.throws(() => loadConfig(["frobnicate"], {}, none), /unknown command/);
  });
});

describe("claude env", () => {
  it("prints a bash block with the slots and unsets Bedrock/Vertex", () => {
    const cfg = loadConfig([], {}, none);
    const out = formatEnv(claudeEnv(cfg, { main: "claude-opus-5.5", opus: "claude-opus-5.5", sonnet: "claude-sonnet-5.5", small: "claude-haiku-4.5" }), "bash");
    assert.match(out, /export ANTHROPIC_BASE_URL='http:\/\/127\.0\.0\.1:4141'/);
    assert.match(out, /export ANTHROPIC_DEFAULT_HAIKU_MODEL='claude-haiku-4\.5'/);
    assert.match(out, /unset CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX/);
    assert.match(formatEnv([["A", "b"]], "powershell"), /\$env:A = "b"/);
  });
});
