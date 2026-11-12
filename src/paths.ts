import os from "node:os";
import path from "node:path";

const APP = "co-claude-bridge";

export function dataDir(env: NodeJS.ProcessEnv = process.env, platform: string = process.platform): string {
  if (env.COB_DATA_DIR) return env.COB_DATA_DIR;
  if (platform === "win32") {
    return path.join(env.APPDATA ?? path.join(os.homedir(), "AppData", "Roaming"), APP);
  }
  return path.join(env.XDG_DATA_HOME ?? path.join(os.homedir(), ".local", "share"), APP);
}

export function configDir(env: NodeJS.ProcessEnv = process.env, platform: string = process.platform): string {
  if (env.COB_CONFIG_DIR) return env.COB_CONFIG_DIR;
  if (platform === "win32") return dataDir(env, platform);
  return path.join(env.XDG_CONFIG_HOME ?? path.join(os.homedir(), ".config"), APP);
}

export const githubTokenPath = () => path.join(dataDir(), "github_token");
export const apiKeyPath = () => path.join(dataDir(), "api_key");
export const prefillCachePath = () => path.join(dataDir(), "prefill.json");
export const configPath = () => path.join(configDir(), "config.json");
