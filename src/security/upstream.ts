// Where credentials may be sent. The GitHub token and the Copilot token are
// only ever sent to hosts that pass these checks, so a tampered token
// response, config file or environment variable can't redirect them.

export class UnsafeUrlError extends Error {}

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

export function isLoopbackHost(host: string): boolean {
  const h = host.toLowerCase();
  return LOOPBACK_HOSTS.has(h) || /^127(\.\d{1,3}){3}$/.test(h);
}

/** Copilot API hosts: githubcopilot.com and its subdomains (api.individual., api.business., ...). */
export function isCopilotHost(host: string): boolean {
  const h = host.toLowerCase();
  return h === "githubcopilot.com" || h.endsWith(".githubcopilot.com");
}

function parse(url: string, what: string): URL {
  try {
    return new URL(url);
  } catch {
    throw new UnsafeUrlError(`${what} is not a valid URL: ${url}`);
  }
}

/**
 * An explicitly configured upstream (--copilot-base-url, COB_GITHUB_API_URL, ...).
 * HTTPS anywhere, or plain HTTP to this machine only (tests, local debugging
 * proxies). Credentials never travel in clear text over a network.
 */
export function checkConfiguredUrl(url: string, what: string): string {
  const u = parse(url, what);
  if (u.username || u.password) throw new UnsafeUrlError(`${what} must not contain a user name or password`);
  if (u.protocol === "https:") return url;
  if (u.protocol === "http:" && isLoopbackHost(u.hostname)) return url;
  throw new UnsafeUrlError(`${what} must use https (plain http is allowed only to localhost): ${url}`);
}

/**
 * The API endpoint the Copilot token response announces. It's data from the
 * network, so it must be an https githubcopilot.com host; anything else is
 * ignored and the account's default endpoint is used.
 */
export function isTrustedCopilotEndpoint(url: string): boolean {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  return u.protocol === "https:" && !u.username && !u.password && isCopilotHost(u.hostname);
}
