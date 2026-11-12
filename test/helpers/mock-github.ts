// A fake github.com + api.github.com: the OAuth device flow, /user and the
// Copilot token exchange. Point COB_GITHUB_BASE_URL and COB_GITHUB_API_URL at it.

import http from "node:http";
import type { AddressInfo } from "node:net";

export const GH_TOKEN = "gho_FakeTokenForTests0123456789abcdefABCD";
export const COPILOT_TOKEN = "tid=fake;exp=9999999999;sku=free;8kp=1:0123456789abcdef";

export interface Seen {
  method: string;
  path: string;
  headers: http.IncomingHttpHeaders;
  body: string;
}

export class MockGitHub {
  readonly seen: Seen[] = [];
  /** Answers for successive POST /login/oauth/access_token calls; the last one repeats. */
  pollAnswers: Array<{ status?: number; body: Record<string, unknown> }> = [{ body: { access_token: GH_TOKEN } }];
  deviceCodeStatus = 200;
  deviceCode: Record<string, unknown> = {
    device_code: "dev-123",
    user_code: "ABCD-1234",
    verification_uri: "https://github.com/login/device",
    expires_in: 900,
    interval: 0,
  };
  /** Status for GET /copilot_internal/v2/token. */
  tokenStatus = 200;
  tokenBody: Record<string, unknown> = { token: COPILOT_TOKEN, expires_at: Math.floor(Date.now() / 1000) + 1800 };
  userStatus = 200;
  validTokens = new Set([GH_TOKEN]);
  private polls = 0;
  private server = http.createServer((req, res) => void this.handle(req, res));
  url = "";

  async start(): Promise<this> {
    await new Promise<void>((r) => this.server.listen(0, "127.0.0.1", () => r()));
    this.url = `http://127.0.0.1:${(this.server.address() as AddressInfo).port}`;
    return this;
  }

  async stop(): Promise<void> {
    this.server.closeAllConnections();
    await new Promise<void>((r) => this.server.close(() => r()));
  }

  /** Set COB_GITHUB_BASE_URL / COB_GITHUB_API_URL to this server; returns a restore function. */
  useInEnv(): () => void {
    const before = { base: process.env.COB_GITHUB_BASE_URL, api: process.env.COB_GITHUB_API_URL };
    process.env.COB_GITHUB_BASE_URL = this.url;
    process.env.COB_GITHUB_API_URL = this.url;
    return () => {
      for (const [k, v] of [["COB_GITHUB_BASE_URL", before.base], ["COB_GITHUB_API_URL", before.api]] as const) {
        if (v === undefined) delete process.env[k];
        else process.env[k] = v;
      }
    };
  }

  private async handle(req: http.IncomingMessage, res: http.ServerResponse) {
    let body = "";
    for await (const c of req) body += c;
    this.seen.push({ method: req.method!, path: req.url!, headers: req.headers, body });
    const json = (status: number, b: unknown) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(b));
    };
    const authed = this.validTokens.has(String(req.headers.authorization ?? "").replace(/^token /, ""));

    if (req.method === "POST" && req.url === "/login/device/code") {
      return json(this.deviceCodeStatus, this.deviceCodeStatus === 200 ? this.deviceCode : { error: "nope" });
    }
    if (req.method === "POST" && req.url === "/login/oauth/access_token") {
      const a = this.pollAnswers[Math.min(this.polls++, this.pollAnswers.length - 1)]!;
      return json(a.status ?? 200, a.body);
    }
    if (req.method === "GET" && req.url === "/user") {
      if (!authed || this.userStatus !== 200) return json(this.userStatus === 200 ? 401 : this.userStatus, { message: "Bad credentials" });
      return json(200, { login: "octocat" });
    }
    if (req.method === "GET" && req.url === "/copilot_internal/v2/token") {
      if (!authed) return json(401, { message: "Bad credentials" });
      return json(this.tokenStatus, this.tokenStatus === 200 ? this.tokenBody : { message: "Resource not accessible" });
    }
    if (req.url === "/redirect") {
      res.writeHead(302, { location: "https://evil.example/" });
      return res.end();
    }
    json(404, { message: "Not Found" });
  }
}
