# Security

co-claude-bridge holds a GitHub OAuth token that can mint Copilot tokens for your
account. This page says what protects that token, the local server, and the published
package, and what is still up to you.

## Reporting a problem

Please report vulnerabilities privately through GitHub's **Report a vulnerability**
button on the repository's Security tab, not in a public issue.

## Credentials

| What | Where it lives | Protection |
| --- | --- | --- |
| GitHub OAuth token (`read:user` scope, from the device flow) | `~/.local/share/co-claude-bridge/github_token` (Windows: `%APPDATA%\co-claude-bridge`) | Directory `0700`, file `0600`. Written to a temp file and renamed, so a planted symlink is replaced, never followed. If the file is found readable by others, it is fixed and you are warned. |
| Copilot API token (about 30 minutes) | Memory only | Sent only to an `https` `githubcopilot.com` host. An endpoint announced by the token response that isn't one is ignored. |
| Bridge API key (`cob_…`, 256 random bits) | `api_key` next to the GitHub token | Same file rules. Required on every API request. |

- Every log line, and every `--log-bodies` file, passes through a redactor that removes
  GitHub tokens (`gho_`, `ghp_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`), Copilot tokens
  (`tid=…`), bridge keys and `Bearer`/`token` header values. Tests run real requests at
  `-vv` and assert that no token appears.
- Requests that carry a token never follow redirects, so a redirect can't forward it.
- `--copilot-base-url`, `COB_GITHUB_BASE_URL` and `COB_GITHUB_API_URL` must be `https`,
  or plain `http` to localhost only.
- Passing `--github-token`, `--copilot-token` or `--api-key` on the command line works,
  but prints a warning: other programs can read a process's arguments. Use the matching
  `COB_*` environment variable instead. A config file holding a credential also warns.

## The local server

A server on `127.0.0.1` is still reachable by every program on the machine and, through
the browser, by any web page you open. The bridge refuses:

- **Requests without the API key** (`x-api-key` or `Authorization: Bearer`, which is what
  Claude Code sends from `ANTHROPIC_AUTH_TOKEN`). Only `/` and `/health`, which reveal
  nothing and call nothing upstream, are open.
- **A `Host` header that isn't a loopback name.** This stops DNS rebinding, where a
  page points its own domain at `127.0.0.1`.
- **Any request with an `Origin` header.** Browsers add it; Claude Code doesn't.
- **POSTs that aren't `application/json`.** A page can't send those cross-site without a
  CORS preflight, which the bridge never approves.
- **Binding to anything but localhost**, unless you pass `--allow-remote`. Even then,
  traffic is unencrypted HTTP.

## Supply chain

The goal is that installing or updating co-claude-bridge can't run someone else's code,
and that a stolen maintainer credential can't publish a release.

**What users install**

- No runtime dependencies. `npx co-claude-bridge` downloads only this package.
- No install scripts (`preinstall`, `postinstall`, `prepare`, ...). A policy test fails
  if one is added.
- The tarball contains only `dist/*.js`, `README.md` and `package.json`.
  `npm run check:pack` checks that list and scans each file for credentials before
  every release.

**Building it**

- `.npmrc` sets `ignore-scripts=true`, so `npm ci` never runs a dependency's install
  script, which is how the Shai-Hulud worm spread. It also sets `save-exact=true` and
  pins the registry.
- Dev dependencies (TypeScript and Node's type definitions) are pinned to exact
  versions. The lockfile is checked: every package comes from `registry.npmjs.org` with
  a `sha512` hash, none has an install script, and none ships to users.
- CI runs `npm audit` and `npm audit signatures`, which verifies registry signatures and
  provenance attestations for every installed package.
- Dependabot proposes updates only after a 7-day cooldown, since compromised releases
  are usually caught and pulled within days.

**GitHub Actions**

- Every action is pinned to a full commit SHA, not a movable tag.
- Workflows default to `permissions: contents: read`. Only the release job gets
  `id-token: write`, and only for publishing.
- `actions/checkout` runs with `persist-credentials: false`, so the token isn't left
  on disk for later steps.
- No `pull_request_target` or `workflow_run` triggers, and no `${{ github.event.* }}`
  inside shell scripts. A policy test enforces each of these on every workflow file.

**Releases**

Releases are published only by `.github/workflows/release.yml`, on a `v*.*.*` tag,
using npm trusted publishing (OIDC). No npm token exists to steal. Every release
carries a provenance statement that ties the tarball to the exact commit and workflow
run, which users can check with `npm audit signatures`.

One-time setup, for the repository owner:

1. On npmjs.com, open the package's **Settings → Trusted publishing** and add GitHub
   Actions with repository `BVMrs/co-claude-proxy`, workflow `release.yml`, and
   environment `npm-publish`.
2. In the same settings, set **Publishing access** to require two-factor
   authentication and disallow tokens.
3. In the GitHub repository, create the `npm-publish` environment with yourself as a
   required reviewer, so pushing a tag alone can't publish.
4. Protect `main`: require pull requests, the CI checks, and code owner review
   (`.github/CODEOWNERS`).
5. Turn on secret scanning and push protection under **Settings → Code security**.

## What stays your responsibility

- Anyone who can run code as your user can read the token files. That's true of every
  CLI that stores a credential (`gh`, `npm`, `docker`).
- The bridge uses unofficial Copilot endpoints. Your GitHub account, not this tool,
  bears the terms-of-service risk.
