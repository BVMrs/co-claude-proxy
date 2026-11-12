import { CopilotAuth, githubTokenFetcher, staticTokenFetcher } from "../auth/copilot-token.ts";
import { getGithubLogin, pollForAccessToken, requestDeviceCode } from "../auth/device-flow.ts";
import { loadOrCreateApiKey, readGithubToken, writeGithubToken } from "../auth/store.ts";
import type { Config } from "../config.ts";
import { CopilotClient } from "../copilot/client.ts";
import { defaultCopilotBaseUrl } from "../copilot/headers.ts";
import { ModelRegistry } from "../copilot/models.ts";
import { color, log, mask } from "../log.ts";
import { Bridge } from "../server/bridge.ts";

export interface Context {
  auth: CopilotAuth;
  client: CopilotClient;
  registry: ModelRegistry;
  bridge: Bridge;
  login?: string;
  /** Key clients must present to the local server. */
  apiKey: string;
}

/** GitHub token from --github-token / COB_GITHUB_TOKEN, then the saved file, else the device flow. */
export async function githubToken(cfg: Config, opts: { allowLogin: boolean; force?: boolean }): Promise<string> {
  if (cfg.githubToken && !opts.force) return cfg.githubToken;
  const saved = opts.force ? undefined : await readGithubToken();
  if (saved) return saved;
  if (!opts.allowLogin) throw new Error("Not signed in. Run `co-claude-bridge auth` first.");
  return deviceLogin();
}

export async function deviceLogin(): Promise<string> {
  const code = await requestDeviceCode();
  log.info(`Sign in to GitHub: open ${color.cyan(code.verification_uri)} and enter ${color.bold(code.user_code)}`);
  const token = await pollForAccessToken(code);
  await writeGithubToken(token);
  log.info("Signed in. Token saved for next time.");
  return token;
}

export async function buildContext(cfg: Config, opts: { allowLogin: boolean }): Promise<Context> {
  let auth: CopilotAuth;
  let login: string | undefined;
  if (cfg.copilotToken) {
    auth = new CopilotAuth(staticTokenFetcher(cfg.copilotToken));
  } else {
    const gh = await githubToken(cfg, opts);
    log.debug(`GitHub token ${mask(gh)}`);
    auth = new CopilotAuth(githubTokenFetcher(gh));
    login = await getGithubLogin(gh).catch(() => undefined);
  }
  await auth.refresh();
  const client = new CopilotClient({
    auth,
    baseUrl: cfg.copilotBaseUrl,
    defaultBaseUrl: defaultCopilotBaseUrl(cfg.accountType),
  });
  const registry = new ModelRegistry(client);
  const bridge = new Bridge(cfg, client, registry);
  const apiKey = cfg.apiKey ?? (await loadOrCreateApiKey());
  return { auth, client, registry, bridge, login, apiKey };
}
