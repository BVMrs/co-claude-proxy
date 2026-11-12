// The real CLI, run as a child process against fake GitHub and Copilot
// servers, with a throwaway HOME and data directory. Nothing here can touch
// the developer's own sign-in.

import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { after, before, beforeEach, describe, it } from "node:test";

import { MockCopilot, MODELS, PREFILL_ERROR } from "../helpers/mock-copilot.ts";
import { COPILOT_TOKEN, GH_TOKEN, MockGitHub } from "../helpers/mock-github.ts";

const CLI = path.resolve("src/cli.ts");
const posix = process.platform !== "win32";

let gh: MockGitHub;
let copilot: MockCopilot;
let home: string;
let dataDir: string;

before(async () => {
  gh = await new MockGitHub().start();
  copilot = await new MockCopilot().start();
  copilot.validTokens = new Set([COPILOT_TOKEN]);
});
after(async () => {
  await gh.stop();
  await copilot.stop();
});
beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), "cob-home-"));
  dataDir = path.join(home, "data");
  gh.tokenStatus = 200;
  copilot.modelsStatus = 200;
  copilot.override = undefined;
});

function env(extra: Record<string, string> = {}): NodeJS.ProcessEnv {
  // Deliberately not inheriting process.env: no real tokens, no real HOME.
  return {
    PATH: process.env.PATH,
    HOME: home,
    USERPROFILE: home,
    NO_COLOR: "1",
    COB_DATA_DIR: dataDir,
    COB_CONFIG: path.join(home, "missing-config.json"),
    COB_GITHUB_BASE_URL: gh.url,
    COB_GITHUB_API_URL: gh.url,
    COB_COPILOT_BASE_URL: copilot.url,
    ...extra,
  };
}

interface Run {
  code: number | null;
  stdout: string;
  stderr: string;
}

function launch(args: string[], extra: Record<string, string> = {}): ChildProcess {
  return spawn(process.execPath, [CLI, ...args], { env: env(extra), cwd: home });
}

function run(args: string[], extra: Record<string, string> = {}): Promise<Run> {
  const child = launch(args, extra);
  let stdout = "";
  let stderr = "";
  child.stdout!.on("data", (c) => (stdout += c));
  child.stderr!.on("data", (c) => (stderr += c));
  return new Promise((resolve) => child.on("close", (code) => resolve({ code, stdout, stderr })));
}

const saveToken = () => {
  fs.mkdirSync(dataDir, { recursive: true });
  fs.writeFileSync(path.join(dataDir, "github_token"), GH_TOKEN + "\n", { mode: 0o600 });
};

describe("cli basics", () => {
  it("prints the version from package.json", async () => {
    const r = await run(["--version"]);
    assert.equal(r.code, 0);
    assert.equal(r.stdout.trim(), JSON.parse(fs.readFileSync("package.json", "utf8")).version);
  });

  it("prints help", async () => {
    for (const args of [["--help"], ["-h"], ["help"]]) {
      const r = await run(args);
      assert.equal(r.code, 0);
      assert.match(r.stdout, /Usage: co-claude-bridge/);
      assert.match(r.stdout, /--allow-remote|--api-key/);
    }
  });

  it("exits 2 on a bad flag", async () => {
    const r = await run(["--nope"]);
    assert.equal(r.code, 2);
    assert.match(r.stderr, /unknown flag --nope/);
  });

  it("refuses to listen beyond localhost without --allow-remote", async () => {
    const r = await run(["--host", "0.0.0.0"]);
    assert.equal(r.code, 2);
    assert.match(r.stderr, /--allow-remote/);
  });

  it("warns when a credential is passed on the command line", async () => {
    const r = await run(["logout", "--github-token", GH_TOKEN]);
    assert.equal(r.code, 0);
    assert.match(r.stderr, /visible to other programs.*COB_GITHUB_TOKEN/);
    assert.match(r.stderr, /No saved sign-in/);
    assert.equal(r.stderr.includes(GH_TOKEN), false);
  });
});

describe("sign-in", () => {
  it("auth runs the device flow, saves an owner-only token and never prints it", async () => {
    const r = await run(["auth", "-vv"]);
    assert.equal(r.code, 0, r.stderr);
    assert.match(r.stderr, /enter ABCD-1234/);
    assert.match(r.stderr, /Signed in as octocat/);
    const file = path.join(dataDir, "github_token");
    assert.equal(fs.readFileSync(file, "utf8").trim(), GH_TOKEN);
    if (posix) assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    assert.equal((r.stdout + r.stderr).includes(GH_TOKEN), false);

    const out = await run(["logout"]);
    assert.match(out.stderr, /Removed .*github_token/);
    assert.equal(fs.existsSync(file), false);
  });

  it("fails with a clear message when the account has no Copilot", async () => {
    saveToken();
    gh.tokenStatus = 403;
    const r = await run(["start", "--port", "0"], { COB_DEBUG: "1" });
    assert.equal(r.code, 1);
    assert.match(r.stderr, /no Copilot access \(403\)/);
    assert.match(r.stderr, /at /, "COB_DEBUG adds the stack");
  });
});

describe("env and models", () => {
  it("env prints the Claude Code settings with this install's API key", async () => {
    saveToken();
    const r = await run(["env", "-vv"]);
    assert.equal(r.code, 0, r.stderr);
    const key = fs.readFileSync(path.join(dataDir, "api_key"), "utf8").trim();
    assert.match(key, /^cob_/);
    assert.ok(r.stdout.includes(`export ANTHROPIC_AUTH_TOKEN='${key}'`), r.stdout);
    assert.match(r.stdout, /export ANTHROPIC_MODEL='claude-opus-5\.5'/);
    assert.equal(r.stderr.includes(GH_TOKEN), false, "debug output masks the GitHub token");
    assert.equal(r.stderr.includes(COPILOT_TOKEN), false);

    const again = await run(["env", "--shell", "fish"]);
    assert.ok(again.stdout.includes(`set -gx ANTHROPIC_AUTH_TOKEN '${key}'`), "the key is stable across runs");
    const ps = await run(["env", "--shell", "powershell"]);
    assert.match(ps.stdout, /Remove-Item Env:ANTHROPIC_API_KEY/);
  });

  it("models lists the account's chat models with prefill guesses", async () => {
    saveToken();
    const r = await run(["models"]);
    assert.equal(r.code, 0, r.stderr);
    assert.match(r.stdout, /^MODEL\s+VENDOR\s+CONTEXT\s+TOOLS\s+PREFILL/m);
    assert.match(r.stdout, /claude-opus-5\.5\s+Anthropic\s+200k\s+yes\s+rejects\?/);
    assert.match(r.stdout, /claude-haiku-4\.5\s+Anthropic\s+yes\s+accepts\?/);
    assert.doesNotMatch(r.stdout, /embedding/);
  });
});

describe("doctor", () => {
  it("passes on a healthy setup and flags settings that bypass the bridge", async () => {
    saveToken();
    fs.mkdirSync(path.join(home, ".claude"));
    fs.writeFileSync(path.join(home, ".claude", "settings.json"), JSON.stringify({ env: { CLAUDE_CODE_USE_BEDROCK: "1" } }));
    const r = await run(["doctor"], { CLAUDE_CODE_USE_VERTEX: "1", ANTHROPIC_API_KEY: "sk-ant-x" });
    assert.equal(r.code, 0, r.stdout + r.stderr);
    assert.match(r.stdout, /CLAUDE_CODE_USE_VERTEX is set/);
    assert.match(r.stdout, /ANTHROPIC_API_KEY is set/);
    assert.match(r.stdout, /settings\.json sets CLAUDE_CODE_USE_BEDROCK=1/);
    assert.match(r.stdout, /Copilot token issued for octocat/);
    assert.match(r.stdout, /Skipped the live prefill probe on 3 Claude models/);
  });

  it("says so when nothing conflicts", async () => {
    saveToken();
    const r = await run(["doctor"]);
    assert.match(r.stdout, /No environment or settings/);
  });

  it("--probe records which models accept prefill and reports odd answers", async () => {
    saveToken();
    MODELS.push({ id: "claude-opus-4.1", name: "Old", vendor: "Anthropic", capabilities: { type: "chat", supports: { tool_calls: true } } });
    copilot.override = (req) => {
      if (req.model === "claude-opus-4.1") return { status: 400, body: { error: { message: "The requested model is not supported.", code: "model_not_supported" } } };
      if (req.model === "claude-sonnet-5.5" && req.messages.at(-1)?.role === "assistant") return { status: 418, body: { error: { message: "teapot" } } };
      return undefined;
    };
    try {
      const r = await run(["doctor", "--probe"]);
      assert.equal(r.code, 1, "an unexplained probe answer is a failure");
      assert.match(r.stdout, /claude-haiku-4\.5: accepts prefill/);
      assert.match(r.stdout, /claude-opus-5\.5: rejects prefill/);
      assert.match(r.stdout, /claude-opus-4\.1: unusable \(400 model_not_supported\)/);
      assert.match(r.stdout, /claude-sonnet-5\.5: prefill probe returned 418 teapot/);
      const saved = JSON.parse(fs.readFileSync(path.join(dataDir, "prefill.json"), "utf8"));
      assert.deepEqual(saved, { "claude-haiku-4.5": false, "claude-opus-5.5": true });
      assert.ok(!PREFILL_ERROR.includes("teapot"));
    } finally {
      MODELS.pop();
    }
  });

  it("--probe exits 0 when every model answers as expected, and reports unusable ones", async () => {
    saveToken();
    copilot.override = (req) => (req.model === "claude-sonnet-5.5" ? { status: 400, body: { error: { message: "nope" } } } : undefined);
    const r = await run(["doctor", "--probe"]);
    assert.equal(r.code, 0, r.stdout);
    assert.match(r.stdout, /claude-sonnet-5\.5: unusable \(400 nope\)/);
  });

  it("fails when sign-in, the model list or the slots are broken", async () => {
    saveToken();
    gh.tokenStatus = 403;
    assert.equal((await run(["doctor"])).code, 1);
    gh.tokenStatus = 200;
    const slots = await run(["doctor", "--model", "claude-opus-1"]);
    assert.equal(slots.code, 1);
    assert.match(slots.stdout, /claude-opus-1 isn't available/);
    copilot.modelsStatus = 500;
    const models = await run(["doctor"]);
    assert.equal(models.code, 1);
    assert.match(models.stdout, /Couldn't load \/models/);
  });
});

describe("start", () => {
  it("serves Claude Code with the printed key and shuts down on SIGTERM", { skip: !posix }, async () => {
    saveToken();
    const child = launch(["--port", "0", "--github-token", GH_TOKEN], { COB_API_KEY: "cob_cli-test-key-0123456789" });
    let stderr = "";
    child.stderr!.on("data", (c) => (stderr += c));
    const exited = new Promise<number | null>((r) => child.on("close", (code) => r(code)));
    const url = await new Promise<string>((resolve, reject) => {
      const t = setTimeout(() => reject(new Error(`no listen line:\n${stderr}`)), 10_000);
      child.stderr!.on("data", () => {
        const m = /listening on (http:\/\/127\.0\.0\.1:\d+)/.exec(stderr);
        if (m && /^claude$/m.test(stderr)) {
          clearTimeout(t);
          resolve(m[1]!);
        }
      });
    });
    assert.match(stderr, /export ANTHROPIC_AUTH_TOKEN='cob_cli-test-key-0123456789'/);
    assert.match(stderr, /--github-token on the command line is visible/);
    const res = await fetch(`${url}/v1/messages`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: "Bearer cob_cli-test-key-0123456789" },
      body: JSON.stringify({ model: "claude-opus-5.5", max_tokens: 8, messages: [{ role: "user", content: "x" }] }),
    });
    assert.equal(res.status, 200);
    child.kill("SIGTERM");
    assert.equal(await exited, 0);
    assert.match(stderr, /Shutting down/);
    assert.equal(stderr.includes(GH_TOKEN), false);
  });
});
