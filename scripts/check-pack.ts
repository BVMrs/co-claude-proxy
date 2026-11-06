// Release gate: check exactly what `npm publish` would upload.
// Run after `npm run build`. Fails if the tarball holds anything but the
// compiled CLI, the README and package.json, or if any file looks like it
// contains a credential.

import { execFileSync } from "node:child_process";
import fs from "node:fs";

const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const [pack] = JSON.parse(execFileSync(npm, ["pack", "--dry-run", "--json", "--ignore-scripts"], { encoding: "utf8" })) as Array<{
  files: Array<{ path: string; size: number }>;
}>;

const ALLOWED = [/^package\.json$/, /^README\.md$/, /^LICENSE(\.md)?$/, /^dist\/[\w/.-]+\.js$/];
const SECRET = /\bgh[pousr]_[A-Za-z0-9]{16,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\btid=[A-Za-z0-9]{8,};exp=\d|\bcob_[A-Za-z0-9_-]{40,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bnpm_[A-Za-z0-9]{36}\b/;

const problems: string[] = [];
const paths = pack!.files.map((f) => f.path).sort();
for (const p of paths) {
  if (!ALLOWED.some((re) => re.test(p))) problems.push(`unexpected file in the package: ${p}`);
  else if (SECRET.test(fs.readFileSync(p, "utf8"))) problems.push(`${p} looks like it contains a credential`);
}
if (!paths.includes("dist/cli.js")) problems.push("dist/cli.js is missing (run npm run build first)");
else if (!fs.readFileSync("dist/cli.js", "utf8").startsWith("#!/usr/bin/env node\n")) problems.push("dist/cli.js lost its shebang");

if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log(`package contents OK (${paths.length} files):\n  ${paths.join("\n  ")}`);
