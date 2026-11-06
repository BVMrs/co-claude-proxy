import os from "node:os";
import path from "node:path";

const APP = "co-claude-bridge";

export function dataDir(): string {
  if (process.env.COB_DATA_DIR) return process.env.COB_DATA_DIR;
  if (process.platform === "win32") {
    return path.join(process.env.APPDATA ?? path.join(os.homedir(), "AppData", "Roaming"), APP);
  }
  return path.join(process.env.XDG_DATA_HOME ?? path.join(os.homedir(), ".local", "share"), APP);
}

export function configDir(): string {
  if (process.env.COB_CONFIG_DIR) return process.env.COB_CONFIG_DIR;
  if (process.platform === "win32") return dataDir();
  return path.join(process.env.XDG_CONFIG_HOME ?? path.join(os.homedir(), ".config"), APP);
}

export const githubTokenPath = () => path.join(dataDir(), "github_token");
export const prefillCachePath = () => path.join(dataDir(), "prefill.json");
export const configPath = () => path.join(configDir(), "config.json");
