import fs from "node:fs";
import path from "node:path";

import { log } from "../log.ts";

/** --log-bodies: write each request and response to numbered files for replay. */
export class BodyLog {
  private readonly dir?: string;

  constructor(dir?: string) {
    this.dir = dir;
    if (dir) fs.mkdirSync(dir, { recursive: true });
  }

  get enabled(): boolean {
    return Boolean(this.dir);
  }

  write(seq: number, name: string, data: unknown): void {
    if (!this.dir) return;
    const file = path.join(this.dir, `${String(seq).padStart(4, "0")}-${name}`);
    const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
    fs.promises.writeFile(file, text, { mode: 0o600 }).catch((e: Error) => log.warn(`could not write ${file}: ${e.message}`));
  }
}
