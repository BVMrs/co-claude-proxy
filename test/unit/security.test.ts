import assert from "node:assert/strict";
import fs from "node:fs";
import type http from "node:http";
import os from "node:os";
import path from "node:path";
import { describe, it } from "node:test";

import {
  deleteGithubToken,
  loadOrCreateApiKey,
  newApiKey,
  readGithubToken,
  readSecret,
  writeGithubToken,
  writeSecret,
} from "../../src/auth/store.ts";
import { githubApiUrl, githubBaseUrl } from "../../src/copilot/headers.ts";
import { mask, redact } from "../../src/log.ts";
import {
  checkConfiguredUrl,
  isCopilotHost,
  isLoopbackHost,
  isTrustedCopilotEndpoint,
  UnsafeUrlError,
} from "../../src/security/upstream.ts";
import { HttpError } from "../../src/server/bridge.ts";
import { checkRequest, keyMatches, presentedKey } from "../../src/server/guard.ts";
import { logLines } from "../helpers/bridge.ts";

const posix = process.platform !== "win32";
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "cob-sec-"));

describe("redact", () => {
  const cases: Array<[string, string]> = [
    ["token gho_AbCdEfGhIjKlMnOpQrStUvWxYz012345 here", "token [redacted] here"],
    ["ghp_AbCdEfGhIjKlMnOpQrStUvWxYz012345", "[redacted]"],
    ["ghu_AbCdEfGhIjKlMnOpQrSt and ghs_AbCdEfGhIjKlMnOpQrSt", "[redacted] and [redacted]"],
    ["pat github_pat_11ABCDEFG0123456789_abcdefghijklmnop", "pat [redacted]"],
    ['{"token":"tid=abc;exp=123;sku=x;8kp=1:deadbeef","x":1}', '{"token":"[redacted]","x":1}'],
    ["key cob_AAAAAAAAAAAAAAAAAAAAAAAA-_", "key [redacted]"],
    ["authorization: Bearer abcdefghijklmnopqrstuvwxyz", "authorization: Bearer [redacted]"],
    ["Authorization: token 0123456789abcdef0123", "Authorization: token [redacted]"],
  ];
  for (const [input, want] of cases) {
    it(`scrubs ${want.includes("Bearer") ? "a bearer header" : input.slice(0, 12)}`, () => assert.equal(redact(input), want));
  }
  it("leaves ordinary text alone", () => {
    const s = "model claude-opus-5.5 returned 200 in 1.2s; token refreshed";
    assert.equal(redact(s), s);
  });
  it("is applied to every log line", async () => {
    const { log } = await import("../../src/log.ts");
    logLines.length = 0;
    log.error(new Error("upstream said: Bearer abcdefghijklmnopqrstuvwxyz0123"));
    log.info("saved", { token: "gho_AbCdEfGhIjKlMnOpQrStUvWxYz012345" });
    assert.ok(logLines.every((l) => !/abcdefghijklmnop|AbCdEfGh/.test(l)), logLines.join("\n"));
  });
  it("mask keeps only the ends of long secrets", () => {
    assert.equal(mask(undefined), "(none)");
    assert.equal(mask("short"), "****");
    assert.equal(mask("abcdefghijklmnop"), "abcd…mnop");
  });
});

describe("upstream URL checks", () => {
  it("knows loopback and Copilot hosts", () => {
    for (const h of ["localhost", "LOCALHOST", "127.0.0.1", "127.1.2.3", "::1", "[::1]"]) assert.ok(isLoopbackHost(h), h);
    for (const h of ["0.0.0.0", "192.168.1.2", "localhost.evil.com", "127.0.0.1.nip.io"]) assert.ok(!isLoopbackHost(h), h);
    for (const h of ["githubcopilot.com", "api.githubcopilot.com", "api.business.githubcopilot.com"]) assert.ok(isCopilotHost(h), h);
    for (const h of ["evilgithubcopilot.com", "githubcopilot.com.evil.com"]) assert.ok(!isCopilotHost(h), h);
  });
  it("only trusts https githubcopilot.com endpoints from the token response", () => {
    assert.ok(isTrustedCopilotEndpoint("https://api.individual.githubcopilot.com"));
    assert.ok(isTrustedCopilotEndpoint("https://api.business.githubcopilot.com/"));
    assert.ok(!isTrustedCopilotEndpoint("http://api.githubcopilot.com"));
    assert.ok(!isTrustedCopilotEndpoint("https://api.githubcopilot.com.evil.com"));
    assert.ok(!isTrustedCopilotEndpoint("https://user:pw@api.githubcopilot.com"));
    assert.ok(!isTrustedCopilotEndpoint("not a url"));
  });
  it("accepts configured https anywhere and http to localhost only", () => {
    assert.equal(checkConfiguredUrl("https://proxy.example.com", "x"), "https://proxy.example.com");
    assert.equal(checkConfiguredUrl("http://127.0.0.1:9", "x"), "http://127.0.0.1:9");
    assert.throws(() => checkConfiguredUrl("http://proxy.example.com", "x"), UnsafeUrlError);
    assert.throws(() => checkConfiguredUrl("ftp://localhost", "x"), /must use https/);
    assert.throws(() => checkConfiguredUrl("https://a:b@example.com", "x"), /user name or password/);
    assert.throws(() => checkConfiguredUrl("::", "x"), /not a valid URL/);
  });
  it("validates GitHub URL overrides from the environment", () => {
    assert.equal(githubBaseUrl({}), "https://github.com");
    assert.equal(githubApiUrl({}), "https://api.github.com");
    assert.equal(githubApiUrl({ COB_GITHUB_API_URL: "https://ghe.example.com/api/v3/" }), "https://ghe.example.com/api/v3");
    assert.equal(githubBaseUrl({ COB_GITHUB_BASE_URL: "http://localhost:1" }), "http://localhost:1");
    assert.throws(() => githubApiUrl({ COB_GITHUB_API_URL: "http://attacker.example" }), UnsafeUrlError);
    assert.throws(() => githubBaseUrl({ COB_GITHUB_BASE_URL: "http://attacker.example" }), UnsafeUrlError);
  });
});

describe("request guard", () => {
  const KEY = "cob_0123456789abcdef0123456789";
  const req = (headers: http.IncomingHttpHeaders, method = "POST") => ({ method, headers }) as http.IncomingMessage;
  const ok = { host: "127.0.0.1:4141", "x-api-key": KEY, "content-type": "application/json" };
  const refuse = (h: http.IncomingHttpHeaders, status: number, route = "POST /v1/messages", opts = {}) => {
    assert.throws(
      () => checkRequest(req(h, route.split(" ")[0]), route, { apiKey: KEY, ...opts }),
      (e: unknown) => e instanceof HttpError && e.status === status,
    );
  };

  it("lets a well-formed local request through", () => {
    checkRequest(req(ok), "POST /v1/messages", { apiKey: KEY });
    checkRequest(req({ ...ok, host: "localhost" }), "POST /v1/messages", { apiKey: KEY });
    checkRequest(req({ ...ok, host: "[::1]:4141" }), "POST /v1/messages", { apiKey: KEY });
    checkRequest(req({ ...ok, "content-type": "Application/JSON; charset=utf-8" }), "POST /v1/messages", { apiKey: KEY });
  });
  it("blocks DNS rebinding through the Host header", () => {
    refuse({ ...ok, host: "evil.example:4141" }, 403);
    refuse({ ...ok, host: "localhost.evil.example" }, 403);
    refuse({ ...ok, host: undefined }, 400);
    checkRequest(req({ ...ok, host: "bridge.lan:4141" }), "POST /v1/messages", { apiKey: KEY, allowRemote: true });
  });
  it("refuses anything a browser sent", () => {
    refuse({ ...ok, origin: "https://evil.example" }, 403);
    refuse({ ...ok, origin: "null" }, 403);
    refuse({ host: "127.0.0.1", origin: "http://127.0.0.1" }, 403, "GET /health");
  });
  it("needs the API key on every API route", () => {
    refuse({ ...ok, "x-api-key": undefined }, 401);
    refuse({ ...ok, "x-api-key": "wrong" }, 401);
    refuse({ ...ok, "x-api-key": KEY + "x" }, 401);
    refuse({ host: "127.0.0.1" }, 401, "GET /v1/models");
    refuse({ host: "127.0.0.1" }, 401, "GET /nope");
    checkRequest(req({ host: "127.0.0.1" }, "GET"), "GET /health", { apiKey: KEY });
    checkRequest(req({ host: "127.0.0.1" }, "HEAD"), "HEAD /", { apiKey: KEY });
  });
  it("refuses form and text bodies that skip a CORS preflight", () => {
    refuse({ ...ok, "content-type": "text/plain" }, 415);
    refuse({ ...ok, "content-type": "application/x-www-form-urlencoded" }, 415);
    refuse({ ...ok, "content-type": undefined }, 415);
  });
  it("reads the key from x-api-key or a bearer header", () => {
    assert.equal(presentedKey({ "x-api-key": " k " }), "k");
    assert.equal(presentedKey({ authorization: "Bearer abc" }), "abc");
    assert.equal(presentedKey({ authorization: "bearer  abc " }), "abc");
    assert.equal(presentedKey({ authorization: "Basic abc" }), undefined);
    assert.equal(presentedKey({}), undefined);
    assert.ok(keyMatches("a", "a"));
    assert.ok(!keyMatches("a", "b"));
    assert.ok(!keyMatches(undefined, "b"));
  });
});

describe("secret files", () => {
  it("writes owner-only files atomically and reads them back", async () => {
    const dir = path.join(tmp(), "nested");
    const file = path.join(dir, "github_token");
    await writeGithubToken("gho_x", file);
    assert.equal(await readGithubToken(file), "gho_x");
    if (posix) {
      assert.equal(fs.statSync(file).mode & 0o777, 0o600);
      assert.equal(fs.statSync(dir).mode & 0o777, 0o700);
    }
    assert.deepEqual(fs.readdirSync(dir), ["github_token"], "no temp files left behind");
    await writeGithubToken("gho_y", file);
    assert.equal(await readGithubToken(file), "gho_y");
    assert.equal(await deleteGithubToken(file), true);
    assert.equal(await deleteGithubToken(file), false);
    assert.equal(await readGithubToken(file), undefined);
  });

  it("tightens a token file other users could read", { skip: !posix }, async () => {
    const file = path.join(tmp(), "github_token");
    fs.writeFileSync(file, "gho_z\n", { mode: 0o644 });
    fs.chmodSync(file, 0o644);
    logLines.length = 0;
    assert.equal(await readSecret(file), "gho_z");
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    assert.ok(logLines.some((l) => /readable by other users/.test(l)));
  });

  it("tightens a shared data directory on write", { skip: !posix }, async () => {
    const dir = tmp();
    fs.chmodSync(dir, 0o755);
    await writeSecret(path.join(dir, "k"), "v");
    assert.equal(fs.statSync(dir).mode & 0o777, 0o700);
  });

  it("replaces a planted symlink instead of writing through it", { skip: !posix }, async () => {
    const dir = tmp();
    const victim = path.join(dir, "victim.txt");
    fs.writeFileSync(victim, "untouched");
    const file = path.join(dir, "github_token");
    fs.symlinkSync(victim, file);
    assert.equal(await readSecret(file), undefined, "a symlink is not read");
    await writeSecret(file, "gho_secret");
    assert.equal(fs.readFileSync(victim, "utf8"), "untouched");
    assert.ok(fs.lstatSync(file).isFile());
  });

  it("ignores empty files and cleans up after a failed write", async () => {
    const dir = tmp();
    fs.writeFileSync(path.join(dir, "empty"), "  \n");
    assert.equal(await readSecret(path.join(dir, "empty")), undefined);
    fs.mkdirSync(path.join(dir, "taken"));
    fs.writeFileSync(path.join(dir, "taken", "x"), "");
    await assert.rejects(writeSecret(path.join(dir, "taken"), "v"));
    assert.deepEqual(fs.readdirSync(dir).sort(), ["empty", "taken"]);
  });

  it("creates one random API key and keeps it", async () => {
    const file = path.join(tmp(), "api_key");
    const a = await loadOrCreateApiKey(file);
    assert.match(a, /^cob_[A-Za-z0-9_-]{43}$/);
    assert.equal(await loadOrCreateApiKey(file), a);
    assert.notEqual(newApiKey(), newApiKey());
  });
});
