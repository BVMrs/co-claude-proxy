import assert from "node:assert/strict";
import { after, before, beforeEach, describe, it } from "node:test";

import { AuthError, CopilotAuth, githubTokenFetcher, staticTokenFetcher } from "../../src/auth/copilot-token.ts";
import { getGithubLogin, pollForAccessToken, requestDeviceCode, type DeviceCode } from "../../src/auth/device-flow.ts";
import { copilotHeaders, defaultCopilotBaseUrl, githubHeaders } from "../../src/copilot/headers.ts";
import { COPILOT_TOKEN, GH_TOKEN, MockGitHub } from "../helpers/mock-github.ts";

let gh: MockGitHub;
let restore: () => void;
before(async () => {
  gh = await new MockGitHub().start();
  restore = gh.useInEnv();
});
after(async () => {
  restore();
  await gh.stop();
});

const code: DeviceCode = { device_code: "d", user_code: "U", verification_uri: "v", expires_in: 60, interval: 0 };
const noSleep = async () => {};

describe("GitHub device flow", () => {
  beforeEach(() => {
    gh.seen.length = 0;
    gh.deviceCodeStatus = 200;
  });

  it("requests a device code for the Copilot client with read:user only", async () => {
    const c = await requestDeviceCode();
    assert.equal(c.user_code, "ABCD-1234");
    const sent = JSON.parse(gh.seen[0]!.body);
    assert.equal(sent.client_id, "Iv1.b507a08c87ecfe98");
    assert.equal(sent.scope, "read:user");
  });

  it("reports a failed device code request", async () => {
    gh.deviceCodeStatus = 503;
    await assert.rejects(requestDeviceCode(), /device code request failed: 503/);
  });

  it("waits through pending, slow_down and transient errors, then returns the token", async () => {
    gh.pollAnswers = [
      { body: { error: "authorization_pending" } },
      { status: 502, body: {} },
      { body: { error: "slow_down", interval: 0 } },
      { body: { error: "slow_down" } },
      { body: { access_token: GH_TOKEN } },
    ];
    const waits: number[] = [];
    const token = await pollForAccessToken(code, async (ms) => void waits.push(ms));
    assert.equal(token, GH_TOKEN);
    assert.deepEqual(waits, [0, 0, 0, 0, 5000], "slow_down without an interval adds 5s");
  });

  for (const [error, want] of [
    ["expired_token", /code expired/],
    ["access_denied", /cancelled/],
    ["unsupported_grant_type", /sign-in failed: unsupported_grant_type/],
  ] as const) {
    it(`stops on ${error}`, async () => {
      gh.pollAnswers = [{ body: { error } }];
      await assert.rejects(pollForAccessToken(code, noSleep), want);
    });
  }

  it("keeps polling on an empty answer and gives up at the deadline", async () => {
    gh.pollAnswers = [{ body: {} }];
    await assert.rejects(pollForAccessToken({ ...code, expires_in: 0.05, interval: 0.01 }), /code expired/);
  });

  it("uses GitHub's defaults when the code omits interval and expiry", async () => {
    gh.pollAnswers = [{ body: { access_token: GH_TOKEN } }];
    const waits: number[] = [];
    const { interval: _i, expires_in: _e, ...bare } = code;
    await pollForAccessToken(bare as DeviceCode, async (ms) => void waits.push(ms));
    assert.deepEqual(waits, [5000]);
  });

  it("looks up the signed-in login", async () => {
    assert.equal(await getGithubLogin(GH_TOKEN), "octocat");
    assert.equal(await getGithubLogin("gho_wrong"), undefined);
  });
});

describe("Copilot token exchange", () => {
  beforeEach(() => {
    gh.tokenStatus = 200;
  });

  it("trades the GitHub token for a Copilot token, with editor headers", async () => {
    const t = await githubTokenFetcher(GH_TOKEN)();
    assert.equal(t.token, COPILOT_TOKEN);
    const sent = gh.seen.at(-1)!;
    assert.equal(sent.headers.authorization, `token ${GH_TOKEN}`);
    assert.match(String(sent.headers["editor-version"]), /^vscode\//);
  });

  it("explains each failure", async () => {
    await assert.rejects(githubTokenFetcher("gho_wrong")(), (e: Error) => e instanceof AuthError && /auth` to sign in again/.test(e.message));
    gh.tokenStatus = 403;
    await assert.rejects(githubTokenFetcher(GH_TOKEN)(), /no Copilot access \(403\)/);
    gh.tokenStatus = 404;
    await assert.rejects(githubTokenFetcher(GH_TOKEN)(), /no Copilot access \(404\)/);
    gh.tokenStatus = 500;
    await assert.rejects(githubTokenFetcher(GH_TOKEN)(), /token request failed: 500/);
  });

  it("never follows a redirect with the GitHub token attached", async () => {
    const restoreApi = process.env.COB_GITHUB_API_URL;
    process.env.COB_GITHUB_API_URL = `${gh.url}/redirect#`;
    try {
      await assert.rejects(githubTokenFetcher(GH_TOKEN)());
      await assert.rejects(getGithubLogin(GH_TOKEN));
    } finally {
      process.env.COB_GITHUB_API_URL = restoreApi;
    }
  });
});

describe("CopilotAuth", () => {
  it("caches the token until five minutes before expiry", async () => {
    let now = 1000;
    let n = 0;
    const auth = new CopilotAuth(async () => ({ token: `t${++n}`, expires_at: 1000 + 600, endpoints: { api: "https://api.x.githubcopilot.com" } }), () => now);
    assert.equal(auth.expiresAt(), undefined);
    assert.equal(await auth.token(), "t1");
    now = 1000 + 299;
    assert.equal(await auth.token(), "t1");
    now = 1000 + 301;
    assert.equal(await auth.token(), "t2");
    assert.equal(auth.apiEndpoint(), "https://api.x.githubcopilot.com");
    assert.equal(auth.expiresAt(), 1600);
  });

  it("shares one refresh between concurrent callers and recovers from a failure", async () => {
    let calls = 0;
    let fail = true;
    const auth = new CopilotAuth(async () => {
      calls++;
      await new Promise((r) => setTimeout(r, 5));
      if (fail) throw new Error("down");
      return { token: "ok", expires_at: Date.now() / 1000 + 3600 };
    });
    const results = await Promise.allSettled([auth.token(), auth.token(), auth.token()]);
    assert.equal(calls, 1);
    assert.ok(results.every((r) => r.status === "rejected"));
    fail = false;
    assert.equal(await auth.token(), "ok");
    assert.equal(calls, 2);
  });

  it("treats a fixed token as long-lived", async () => {
    const t = await staticTokenFetcher("abc")();
    assert.equal(t.token, "abc");
    assert.ok(t.expires_at > Date.now() / 1000 + 300 * 86400);
  });
});

describe("headers", () => {
  it("identify as VS Code Copilot Chat and carry the right token type", () => {
    assert.equal(githubHeaders("g").authorization, "token g");
    const h = copilotHeaders("c", { vision: true, initiator: "agent" });
    assert.equal(h.authorization, "Bearer c");
    assert.equal(h["copilot-vision-request"], "true");
    assert.equal(h["x-initiator"], "agent");
    assert.notEqual(copilotHeaders("c")["x-request-id"], h["x-request-id"]);
    assert.equal(copilotHeaders("c")["copilot-vision-request"], undefined);
  });
  it("pick the account's Copilot endpoint", () => {
    assert.equal(defaultCopilotBaseUrl("individual"), "https://api.githubcopilot.com");
    assert.equal(defaultCopilotBaseUrl("business"), "https://api.business.githubcopilot.com");
  });
});
