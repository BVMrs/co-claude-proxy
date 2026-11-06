import { githubApiUrl, githubHeaders } from "../copilot/headers.ts";
import { log } from "../log.ts";
import type { CopilotTokenResponse } from "../types/copilot.ts";

export class AuthError extends Error {}

export type TokenFetcher = () => Promise<CopilotTokenResponse>;

/** Exchange a GitHub OAuth token for a short-lived Copilot API token. */
export function githubTokenFetcher(githubToken: string): TokenFetcher {
  return async () => {
    const res = await fetch(`${githubApiUrl()}/copilot_internal/v2/token`, {
      headers: githubHeaders(githubToken),
      redirect: "error",
    });
    if (!res.ok) {
      const text = await res.text();
      if (res.status === 401) {
        throw new AuthError("GitHub rejected the saved sign-in. Run `co-claude-bridge auth` to sign in again.");
      }
      if (res.status === 403 || res.status === 404) {
        throw new AuthError(
          `This GitHub account has no Copilot access (${res.status}). Check that Copilot is enabled for it. ${text.slice(0, 200)}`,
        );
      }
      throw new AuthError(`Copilot token request failed: ${res.status} ${text.slice(0, 300)}`);
    }
    return (await res.json()) as CopilotTokenResponse;
  };
}

export function staticTokenFetcher(token: string): TokenFetcher {
  // A fixed token never expires from our point of view.
  return async () => ({ token, expires_at: Math.floor(Date.now() / 1000) + 365 * 86400 });
}

const REFRESH_MARGIN_S = 5 * 60;

/**
 * Holds the current Copilot token. Refreshes on demand shortly before expiry,
 * and shares one in-flight refresh between concurrent requests.
 */
export class CopilotAuth {
  private current?: CopilotTokenResponse;
  private inflight?: Promise<CopilotTokenResponse>;
  private readonly fetcher: TokenFetcher;
  private readonly now: () => number;

  constructor(fetcher: TokenFetcher, now: () => number = () => Date.now() / 1000) {
    this.fetcher = fetcher;
    this.now = now;
  }

  async token(): Promise<string> {
    const cur = this.current;
    if (cur && cur.expires_at - REFRESH_MARGIN_S > this.now()) return cur.token;
    return (await this.refresh()).token;
  }

  /** API base URL announced in the token response, when present. */
  apiEndpoint(): string | undefined {
    return this.current?.endpoints?.api;
  }

  expiresAt(): number | undefined {
    return this.current?.expires_at;
  }

  refresh(): Promise<CopilotTokenResponse> {
    if (!this.inflight) {
      this.inflight = this.fetcher()
        .then((t) => {
          this.current = t;
          log.debug(`Copilot token refreshed, expires ${new Date(t.expires_at * 1000).toISOString()}`);
          return t;
        })
        .finally(() => {
          this.inflight = undefined;
        });
    }
    return this.inflight;
  }
}
