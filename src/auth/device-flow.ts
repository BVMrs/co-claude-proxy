import { GITHUB_API_URL, GITHUB_BASE_URL, GITHUB_CLIENT_ID, GITHUB_SCOPES, githubHeaders } from "../copilot/headers.ts";

export interface DeviceCode {
  device_code: string;
  user_code: string;
  verification_uri: string;
  expires_in: number;
  interval: number;
}

const jsonHeaders = { accept: "application/json", "content-type": "application/json" };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function requestDeviceCode(): Promise<DeviceCode> {
  const res = await fetch(`${GITHUB_BASE_URL}/login/device/code`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ client_id: GITHUB_CLIENT_ID, scope: GITHUB_SCOPES }),
  });
  if (!res.ok) throw new Error(`GitHub device code request failed: ${res.status} ${await res.text()}`);
  return (await res.json()) as DeviceCode;
}

/** Poll until the user approves the device code. Follows RFC 8628 error codes. */
export async function pollForAccessToken(code: DeviceCode): Promise<string> {
  let interval = (code.interval || 5) * 1000;
  const deadline = Date.now() + (code.expires_in || 900) * 1000;
  while (Date.now() < deadline) {
    await sleep(interval);
    const res = await fetch(`${GITHUB_BASE_URL}/login/oauth/access_token`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({
        client_id: GITHUB_CLIENT_ID,
        device_code: code.device_code,
        grant_type: "urn:ietf:params:oauth:grant-type:device_code",
      }),
    });
    if (!res.ok) continue; // transient; try again at the next interval
    const body = (await res.json()) as { access_token?: string; error?: string; interval?: number };
    if (body.access_token) return body.access_token;
    switch (body.error) {
      case "authorization_pending":
        break;
      case "slow_down":
        interval = (body.interval ?? interval / 1000 + 5) * 1000;
        break;
      case "expired_token":
        throw new Error("The sign-in code expired. Run the command again.");
      case "access_denied":
        throw new Error("Sign-in was cancelled on GitHub.");
      default:
        if (body.error) throw new Error(`GitHub sign-in failed: ${body.error}`);
    }
  }
  throw new Error("The sign-in code expired. Run the command again.");
}

export async function getGithubLogin(githubToken: string): Promise<string | undefined> {
  const res = await fetch(`${GITHUB_API_URL}/user`, { headers: githubHeaders(githubToken) });
  if (!res.ok) return undefined;
  return ((await res.json()) as { login?: string }).login;
}
