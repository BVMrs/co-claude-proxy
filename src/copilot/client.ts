import type { CopilotAuth } from "../auth/copilot-token.ts";
import { log } from "../log.ts";
import { checkConfiguredUrl, isTrustedCopilotEndpoint } from "../security/upstream.ts";
import type { CopilotModelsResponse } from "../types/copilot.ts";
import type { ChatRequest } from "../types/openai.ts";
import { copilotHeaders, type Initiator } from "./headers.ts";

export interface ClientOptions {
  auth: CopilotAuth;
  /** Fixed base URL. Otherwise the token's announced endpoint, then the account default. */
  baseUrl?: string;
  defaultBaseUrl: string;
  maxRetries?: number;
  /** Tests shrink this to keep retries fast. */
  backoffMs?: number;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export class CopilotClient {
  private readonly opts: Required<Omit<ClientOptions, "baseUrl">> & { baseUrl?: string };
  private warnedEndpoint?: string;

  constructor(opts: ClientOptions) {
    if (opts.baseUrl) checkConfiguredUrl(opts.baseUrl, "--copilot-base-url");
    this.opts = { maxRetries: 2, backoffMs: 500, ...opts };
  }

  /**
   * Where the Copilot token goes: --copilot-base-url, else the endpoint the
   * token response announces if it's an https githubcopilot.com host, else
   * the account type's default.
   */
  baseUrl(): string {
    if (this.opts.baseUrl) return this.opts.baseUrl.replace(/\/+$/, "");
    const announced = this.opts.auth.apiEndpoint();
    if (announced && isTrustedCopilotEndpoint(announced)) return announced.replace(/\/+$/, "");
    if (announced && this.warnedEndpoint !== announced) {
      this.warnedEndpoint = announced;
      log.warn(`Ignoring the Copilot API endpoint ${announced} from the token response: not an https githubcopilot.com host`);
    }
    return this.opts.defaultBaseUrl.replace(/\/+$/, "");
  }

  async models(signal?: AbortSignal): Promise<CopilotModelsResponse> {
    const res = await this.send("GET", "/models", undefined, {}, signal);
    if (!res.ok) throw new UpstreamError(res.status, await res.text(), "GET /models");
    return (await res.json()) as CopilotModelsResponse;
  }

  /** POST /chat/completions. Returns the raw response, ok or not; the caller reads the body. */
  chat(
    payload: ChatRequest,
    opts: { vision?: boolean; initiator?: Initiator; signal?: AbortSignal } = {},
  ): Promise<Response> {
    return this.send("POST", "/chat/completions", payload, { vision: opts.vision, initiator: opts.initiator }, opts.signal);
  }

  /**
   * One request with the retries that are safe before any byte reaches the
   * client: a 401/403 forces a token refresh and one retry; 429 and 5xx are
   * retried with backoff, honouring a short Retry-After.
   */
  private async send(
    method: string,
    path: string,
    payload: unknown,
    headerOpts: { vision?: boolean; initiator?: Initiator },
    signal?: AbortSignal,
  ): Promise<Response> {
    let refreshed = false;
    for (let attempt = 0; ; attempt++) {
      const token = await this.opts.auth.token();
      const init: RequestInit = {
        method,
        headers: copilotHeaders(token, headerOpts),
        body: payload === undefined ? undefined : JSON.stringify(payload),
        signal,
        // Never follow a redirect with the Copilot token attached.
        redirect: "error",
      };
      let res: Response;
      try {
        res = await fetch(this.baseUrl() + path, init);
      } catch (e) {
        if (signal?.aborted || attempt >= this.opts.maxRetries) throw e;
        log.debug(`${method} ${path} network error, retrying: ${(e as Error).message}`);
        await sleep(this.opts.backoffMs * 2 ** attempt);
        continue;
      }
      if ((res.status === 401 || res.status === 403) && !refreshed) {
        refreshed = true;
        await res.body?.cancel();
        log.verbose(`${method} ${path} returned ${res.status}; refreshing the Copilot token and retrying`);
        await this.opts.auth.refresh();
        continue;
      }
      if ((res.status === 429 || res.status >= 500) && attempt < this.opts.maxRetries) {
        const retryAfter = Number(res.headers.get("retry-after"));
        const wait =
          Number.isFinite(retryAfter) && retryAfter > 0 && retryAfter <= 10
            ? retryAfter * 1000
            : this.opts.backoffMs * 2 ** attempt;
        await res.body?.cancel();
        log.verbose(`${method} ${path} returned ${res.status}; retry ${attempt + 1} in ${wait}ms`);
        await sleep(wait);
        continue;
      }
      return res;
    }
  }
}

export class UpstreamError extends Error {
  readonly status: number;
  readonly body: string;
  constructor(status: number, body: string, what: string) {
    super(`${what} failed: ${status} ${body.slice(0, 300)}`);
    this.status = status;
    this.body = body;
  }
}
