import fs from "node:fs/promises";
import path from "node:path";

import { githubTokenPath } from "../paths.ts";

export async function readGithubToken(file = githubTokenPath()): Promise<string | undefined> {
  try {
    const t = (await fs.readFile(file, "utf8")).trim();
    return t || undefined;
  } catch {
    return undefined;
  }
}

export async function writeGithubToken(token: string, file = githubTokenPath()): Promise<void> {
  await fs.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  await fs.writeFile(file, token + "\n", { mode: 0o600 });
  await fs.chmod(file, 0o600).catch(() => {});
}

export async function deleteGithubToken(file = githubTokenPath()): Promise<boolean> {
  try {
    await fs.unlink(file);
    return true;
  } catch {
    return false;
  }
}
