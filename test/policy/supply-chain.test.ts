// Repository rules that keep the published package and the build pipeline
// hard to hijack. These run with the normal test suite, so a change that
// weakens one of them fails CI with a reason, instead of slipping through review.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";

import { VERSION } from "../../src/main.ts";

const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));
const lock = JSON.parse(fs.readFileSync("package-lock.json", "utf8"));
const workflowDir = ".github/workflows";
const workflows = fs.readdirSync(workflowDir).filter((f) => /\.ya?ml$/.test(f)).map((f) => ({ name: f, text: fs.readFileSync(path.join(workflowDir, f), "utf8") }));

describe("package.json", () => {
  it("has no runtime dependencies: users install nothing but our own code", () => {
    for (const k of ["dependencies", "optionalDependencies", "peerDependencies", "bundleDependencies", "bundledDependencies"]) {
      assert.equal(pkg[k], undefined, `${k} must stay empty; see SECURITY.md before adding one`);
    }
  });
  it("has no install-time lifecycle scripts", () => {
    for (const k of ["preinstall", "install", "postinstall", "prepare", "preprepare", "postprepare", "prepack", "postpack", "prepublish", "prepublishOnly"]) {
      assert.equal(pkg.scripts?.[k], undefined, `scripts.${k} would run code on someone else's machine or skip review`);
    }
  });
  it("pins every dev dependency to an exact version", () => {
    for (const [name, v] of Object.entries(pkg.devDependencies ?? {})) assert.match(String(v), /^\d+\.\d+\.\d+$/, `${name}@${v}`);
  });
  it("publishes only the compiled CLI and the README", () => {
    assert.deepEqual(pkg.files, ["dist", "README.md"]);
    for (const p of Object.values(pkg.bin)) assert.match(String(p), /^dist\//);
  });
  it("reports the same version as the CLI", () => assert.equal(VERSION, pkg.version));
});

describe("package-lock.json", () => {
  const entries = Object.entries(lock.packages as Record<string, any>).filter(([k]) => k !== "");
  it("is lockfile v3 and lists only dev packages", () => {
    assert.equal(lock.lockfileVersion, 3);
    for (const [k, v] of entries) assert.equal(v.dev, true, `${k} would ship to users`);
  });
  it("resolves every package from the npm registry with a sha512 integrity hash", () => {
    for (const [k, v] of entries) {
      assert.match(String(v.resolved), /^https:\/\/registry\.npmjs\.org\//, `${k} comes from ${v.resolved}`);
      assert.match(String(v.integrity), /^sha512-/, `${k} has no sha512 integrity`);
    }
  });
  it("contains no package with an install script", () => {
    for (const [k, v] of entries) assert.notEqual(v.hasInstallScript, true, `${k} has an install script`);
  });
});

describe(".npmrc", () => {
  const rc = fs.readFileSync(".npmrc", "utf8");
  it("turns off install scripts and saves exact versions", () => {
    assert.match(rc, /^ignore-scripts=true$/m);
    assert.match(rc, /^save-exact=true$/m);
    assert.match(rc, /^registry=https:\/\/registry\.npmjs\.org\/$/m);
  });
  it("holds no auth token", () => assert.doesNotMatch(rc, /_authToken|_auth=|_password/));
});

describe("GitHub Actions workflows", () => {
  it("exist", () => assert.ok(workflows.length > 0));
  for (const w of workflows) {
    describe(w.name, () => {
      it("pins every action to a full commit SHA", () => {
        for (const m of w.text.matchAll(/^\s*-?\s*uses:\s*(\S+)/gm)) {
          const ref = m[1]!;
          if (ref.startsWith("./")) continue;
          assert.match(ref, /^[\w.-]+\/[\w./-]+@[0-9a-f]{40}$/, `${ref} is not pinned to a SHA`);
        }
      });
      it("sets least-privilege permissions at the top level", () => {
        assert.match(w.text, /^permissions:\s*(\{\}|\n\s+contents:\s*read)/m);
        assert.doesNotMatch(w.text, /permissions:\s*write-all|contents:\s*write/);
      });
      it("never runs untrusted PR code with secrets", () => assert.doesNotMatch(w.text, /pull_request_target|workflow_run/));
      it("doesn't leave the GitHub token in the checkout", () => {
        const checkouts = w.text.match(/uses:\s*actions\/checkout@/g)?.length ?? 0;
        const off = w.text.match(/persist-credentials:\s*false/g)?.length ?? 0;
        assert.equal(off, checkouts);
      });
      it("doesn't interpolate event data into shell scripts", () => {
        assert.doesNotMatch(w.text, /\$\{\{\s*github\.(event|head_ref)/);
      });
      it("installs with scripts disabled", () => {
        for (const m of w.text.matchAll(/\bnpm (ci|install)\b[^\n]*/g)) assert.match(m[0], /--ignore-scripts/, m[0]);
      });
    });
  }
});

describe("tracked files", () => {
  // Fake tokens in tests are fine as long as they say so.
  const SECRET = /\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}|\btid=[A-Za-z0-9]{8,};exp=\d|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bnpm_[A-Za-z0-9]{36}\b|\bsk-ant-[A-Za-z0-9_-]{20,}/g;
  const FAKE = /Fake|AbCdEf|0123456789/;
  it("contain no credentials", () => {
    let files: string[];
    try {
      files = execFileSync("git", ["ls-files", "-z"], { encoding: "utf8" }).split("\0").filter(Boolean);
    } catch {
      return; // not a git checkout (e.g. a source tarball)
    }
    const hits: string[] = [];
    for (const f of files) {
      if (!fs.existsSync(f) || fs.statSync(f).size > 2_000_000) continue;
      const text = fs.readFileSync(f, "utf8");
      for (const m of text.matchAll(SECRET)) if (!FAKE.test(m[0])) hits.push(`${f}: ${m[0].slice(0, 10)}…`);
    }
    assert.deepEqual(hits, []);
  });
  it("ignore local secrets and build output", () => {
    const ignore = fs.readFileSync(".gitignore", "utf8");
    for (const p of [".env", "node_modules/", "dist/", "*.tgz"]) assert.ok(ignore.split("\n").includes(p), `.gitignore lacks ${p}`);
  });
});
