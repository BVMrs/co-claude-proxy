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
    const out = formatEnv(claudeEnv(cfg, { main: "claude-opus-5.5", opus: "claude-opus-5.5", sonnet: "claude-sonnet-5.5", small: "claude-haiku-4.5" }, "cob_k"), "bash");
    assert.match(out, /export ANTHROPIC_AUTH_TOKEN='cob_k'/);
    assert.match(out, /export ANTHROPIC_BASE_URL='http:\/\/127\.0\.0\.1:4141'/);
    assert.match(out, /export ANTHROPIC_DEFAULT_HAIKU_MODEL='claude-haiku-4\.5'/);
    assert.match(out, /unset CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX/);
    assert.match(formatEnv([["A", "b"]], "powershell"), /\$env:A = "b"/);
  });
});

describe("loadConfig validation", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cob-cfg-"));
  const file = (name: string, text: string) => {
    const f = path.join(dir, name);
    fs.writeFileSync(f, text);
    return f;
  };

  it("explains every bad value", () => {
    const bad: Array<[string[], RegExp]> = [
      [["--account-type", "team"], /--account-type must be/],
      [["--shell", "zsh"], /--shell must be/],
      [["--port", "70000"], /--port must be 0-65535/],
      [["--port", "abc"], /expects a number/],
      [["--port"], /needs a value/],
      [["--strict-models=maybe"], /expects true or false/],
      [["--alias", "nope"], /--alias expects FROM=TO/],
      [["--alias"], /--alias expects FROM=TO/],
      [["start", "extra"], /unexpected argument "extra"/],
      [["--api-key", "short"], /at least 16 characters/],
    ];
    for (const [argv, re] of bad) assert.throws(() => loadConfig(argv, {}, none), re, argv.join(" "));
    assert.throws(() => loadConfig([], { COB_PORT: "x" }, none), /expects a number/);
  });

  it("reads booleans from flags, negations and the environment", () => {
    assert.equal(loadConfig(["--strict-models"], {}, none).strictModels, true);
    assert.equal(loadConfig(["--strict-models", "--no-strict-models"], {}, none).strictModels, false);
    assert.equal(loadConfig([], { COB_STRICT_MODELS: "yes" }, none).strictModels, true);
    assert.equal(loadConfig([], { COB_WAIT: "off" }, none).wait, false);
    assert.equal(loadConfig([], { COB_VERBOSE: "2" }, none).verbose, 2);
    assert.equal(loadConfig([], { COB_VERBOSE: "yes" }, none).verbose, 1);
    assert.equal(loadConfig(["-v"], {}, none).verbose, 1);
    assert.throws(() => loadConfig(["--no-port"], {}, none), /unknown flag --no-port/);
  });

  it("rejects broken config files", () => {
    assert.throws(() => loadConfig([], {}, file("bad.json", "{")), /not valid JSON/);
    assert.throws(() => loadConfig([], {}, file("arr.json", "null")), /must hold a JSON object/);
    assert.throws(() => loadConfig([], {}, file("key.json", '{"colour":"red"}')), /unknown key "colour"/);
    assert.throws(() => loadConfig([], {}, file("alias.json", '{"modelAliases":[1]}')), /modelAliases must be an object/);
    const c = loadConfig([], { COB_CONFIG: file("ok.json", '{"strictModels":true,"rateLimit":2}') }, none);
    assert.equal(c.strictModels, true);
    assert.equal(c.rateLimit, 2);
  });

  it("warns about credentials in config files and on the command line", () => {
    const c = loadConfig(["--copilot-token", "abc"], {}, file("secret.json", '{"githubToken":"gho_x"}'));
    assert.equal(c.warnings.length, 2);
    assert.match(c.warnings[0]!, /holds githubToken/);
    assert.match(c.warnings[1]!, /--copilot-token on the command line.*COB_COPILOT_TOKEN/);
    assert.deepEqual(loadConfig([], { COB_GITHUB_TOKEN: "gho_x" }, none).warnings, [], "the environment is the recommended way");
  });

  it("only binds beyond loopback with --allow-remote, and warns when it does", () => {
    assert.throws(() => loadConfig(["--host", "0.0.0.0"], {}, none), /--allow-remote/);
    assert.throws(() => loadConfig([], { COB_HOST: "192.168.1.5" }, none), /--allow-remote/);
    const c = loadConfig(["--host", "0.0.0.0", "--allow-remote"], {}, none);
    assert.match(c.warnings.join(), /not encrypted/);
    assert.equal(loadConfig(["--host", "localhost"], {}, none).warnings.length, 0);
    assert.equal(loadConfig(["--host", "::1"], {}, none).host, "::1");
  });

  it("formats env for remote and IPv6 hosts", () => {
    const remote = loadConfig(["--host", "0.0.0.0", "--allow-remote"], {}, none);
    assert.equal(claudeEnv(remote, {}, "k")[0]![1], "http://127.0.0.1:4141");
    const v6 = loadConfig(["--host", "::1"], {}, none);
    assert.equal(claudeEnv(v6, {}, "k")[0]![1], "http://[::1]:4141");
    assert.match(formatEnv([["A", "it's"]], "bash"), /export A='it'\\''s'/);
    assert.match(formatEnv([["A", 'say "hi"']], "powershell"), /\$env:A = "say `"hi`""/);
  });
});

describe("githubToken source order", () => {
  it("prefers the flag, then the saved file, and won't start a login when told not to", async () => {
    const { githubToken } = await import("../../src/commands/context.ts");
    const before = process.env.COB_DATA_DIR;
    process.env.COB_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "cob-gt-"));
    try {
      const cfg = loadConfig([], {}, none);
      await assert.rejects(githubToken(cfg, { allowLogin: false }), /Not signed in/);
      fs.writeFileSync(path.join(process.env.COB_DATA_DIR, "github_token"), "gho_saved\n", { mode: 0o600 });
      assert.equal(await githubToken(cfg, { allowLogin: false }), "gho_saved");
      assert.equal(await githubToken({ ...cfg, githubToken: "gho_flag" }, { allowLogin: false }), "gho_flag");
      await assert.rejects(githubToken({ ...cfg, githubToken: "gho_flag" }, { allowLogin: false, force: true }), /Not signed in/);
    } finally {
      if (before === undefined) delete process.env.COB_DATA_DIR;
      else process.env.COB_DATA_DIR = before;
    }
  });
});
