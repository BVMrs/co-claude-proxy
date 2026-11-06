// Who may use the local server. A bridge on 127.0.0.1 is still reachable by
// every program on the machine and, through the browser, by every web page
// the user opens. Each check below closes one of those doors:
//
// - Host header: a page that rebinds its own DNS name to 127.0.0.1 sends its
//   own name as Host, so only loopback names are accepted (DNS rebinding).
// - Origin header: browsers add it to cross-site requests; Claude Code and
//   other API clients don't send it, so any request carrying one is refused.
// - Content-Type: POSTs must be application/json, which a page can't send
//   cross-site without a CORS preflight this server never approves.
// - API key: every API route needs the per-install key, so other local users
//   and programs can't spend the Copilot subscription.

import { createHash, timingSafeEqual } from "node:crypto";
import type http from "node:http";

import { isLoopbackHost } from "../security/upstream.ts";
import { HttpError } from "./bridge.ts";

export interface GuardOptions {
  apiKey: string;
  /** --allow-remote: accept any Host header. */
  allowRemote?: boolean;
  /** Largest request body accepted, in bytes. */
  maxBodyBytes?: number;
}

/** Routes that reveal nothing and cost nothing, so health checks work without the key. */
const PUBLIC_ROUTES = new Set(["GET /", "HEAD /", "GET /health"]);

const digest = (s: string) => createHash("sha256").update(s).digest();

/** Constant-time comparison of a presented key with the expected one. */
export function keyMatches(presented: string | undefined, expected: string): boolean {
  if (!presented) return false;
  return timingSafeEqual(digest(presented), digest(expected));
}

/** The key a client sent: x-api-key (Anthropic SDKs) or Authorization: Bearer (ANTHROPIC_AUTH_TOKEN). */
export function presentedKey(headers: http.IncomingHttpHeaders): string | undefined {
  const x = headers["x-api-key"];
  if (typeof x === "string" && x) return x.trim();
  const auth = headers.authorization;
  const m = typeof auth === "string" ? /^Bearer\s+(.+)$/i.exec(auth) : null;
  return m?.[1]?.trim();
}

function hostname(hostHeader: string): string {
  // "[::1]:4141" -> "[::1]", "localhost:4141" -> "localhost"
  if (hostHeader.startsWith("[")) return hostHeader.slice(0, hostHeader.indexOf("]") + 1);
  return hostHeader.replace(/:\d*$/, "");
}

/** Throws an HttpError when the request must be refused. */
export function checkRequest(req: http.IncomingMessage, route: string, opts: GuardOptions): void {
  const host = req.headers.host;
  if (!host) throw new HttpError(400, "Missing Host header.");
  if (!opts.allowRemote && !isLoopbackHost(hostname(host))) {
    throw new HttpError(403, `co-claude-bridge only answers requests addressed to localhost, not ${hostname(host)}.`);
  }
  if (req.headers.origin !== undefined) {
    throw new HttpError(403, "co-claude-bridge doesn't accept requests from web pages.");
  }
  if (PUBLIC_ROUTES.has(route)) return;
  if (!keyMatches(presentedKey(req.headers), opts.apiKey)) {
    throw new HttpError(
      401,
      "Missing or wrong API key. Set ANTHROPIC_AUTH_TOKEN to the key that `co-claude-bridge env` prints.",
    );
  }
  if (req.method === "POST") {
    const type = String(req.headers["content-type"] ?? "").split(";")[0]!.trim().toLowerCase();
    if (type !== "application/json") throw new HttpError(415, "Content-Type must be application/json.");
  }
}
