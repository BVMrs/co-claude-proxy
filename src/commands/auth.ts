import { getGithubLogin } from "../auth/device-flow.ts";
import { deleteGithubToken } from "../auth/store.ts";
import { log } from "../log.ts";
import { githubTokenPath } from "../paths.ts";
import { deviceLogin } from "./context.ts";

export async function auth(): Promise<void> {
  const token = await deviceLogin();
  const login = await getGithubLogin(token).catch(() => undefined);
  if (login) log.info(`Signed in as ${login}`);
}

export async function logout(): Promise<void> {
  const removed = await deleteGithubToken();
  log.info(removed ? `Removed ${githubTokenPath()}` : "No saved sign-in to remove.");
}
