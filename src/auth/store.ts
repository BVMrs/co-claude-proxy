import { randomBytes } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

import { log } from "../log.ts";
import { apiKeyPath, githubTokenPath } from "../paths.ts";

// Secrets live in owner-only files: the directory is 0700 and each file 0600.
// Writes go to a temporary file that is renamed into place, so a crash never
// leaves half a token, and a symlink planted at the target is replaced
// instead of followed. Windows has no POSIX modes; there the files rely on
// the per-user profile directory's ACLs.

const POSIX = process.platform !== "win32";

/** Read a secret file. Tightens its permissions if another user could read it. */
export async function readSecret(file: string): Promise<string | undefined> {
  let st;
  try {
    st = await fs.lstat(file);
  } catch {
    return undefined;
  }
  if (!st.isFile()) {
    log.warn(`Ignoring ${file}: it is not a regular file`);
    return undefined;
  }
  if (POSIX && (st.mode & 0o077) !== 0) {
    log.warn(`${file} was readable by other users (mode ${(st.mode & 0o777).toString(8)}); restricting it to you`);
    await fs.chmod(file, 0o600);
  }
  const t = (await fs.readFile(file, "utf8")).trim();
  return t || undefined;
}

export async function writeSecret(file: string, value: string): Promise<void> {
  const dir = path.dirname(file);
  await fs.mkdir(dir, { recursive: true, mode: 0o700 });
  if (POSIX) await fs.chmod(dir, 0o700);
  const tmp = path.join(dir, `.${path.basename(file)}.${process.pid}.${randomBytes(6).toString("hex")}.tmp`);
  try {
    await fs.writeFile(tmp, value + "\n", { mode: 0o600, flag: "wx" });
    await fs.rename(tmp, file);
  } catch (e) {
    await fs.rm(tmp, { force: true });
    throw e;
  }
}

export async function deleteSecret(file: string): Promise<boolean> {
  try {
    await fs.unlink(file);
    return true;
  } catch {
    return false;
  }
}

export const readGithubToken = (file = githubTokenPath()) => readSecret(file);
export const writeGithubToken = (token: string, file = githubTokenPath()) => writeSecret(file, token);
export const deleteGithubToken = (file = githubTokenPath()) => deleteSecret(file);

/** A fresh random key for clients of the local server: "cob_" + 256 bits. */
export function newApiKey(): string {
  return `cob_${randomBytes(32).toString("base64url")}`;
}

/** The local server's API key, created on first use and kept for later runs. */
export async function loadOrCreateApiKey(file = apiKeyPath()): Promise<string> {
  const existing = await readSecret(file);
  if (existing) return existing;
  const key = newApiKey();
  await writeSecret(file, key);
  return key;
}
