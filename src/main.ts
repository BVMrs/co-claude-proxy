import { auth, logout } from "./commands/auth.ts";
import { buildContext } from "./commands/context.ts";
import { doctor } from "./commands/doctor.ts";
import { claudeEnv, formatEnv } from "./commands/env.ts";
import { models } from "./commands/models.ts";
import { start } from "./commands/start.ts";
import { ConfigError, loadConfig } from "./config.ts";
import { log, setLevel } from "./log.ts";

export const VERSION = "0.1.0";

export const HELP = `co-claude-bridge ${VERSION}
Run Claude Code on a GitHub Copilot subscription.

Usage: co-claude-bridge [command] [flags]

Commands:
  start     Sign in if needed and run the bridge (default)
  auth      Sign in to GitHub again and save the token
  logout    Delete the saved GitHub token
  models    List the account's models and whether each accepts prefill
  env       Print the Claude Code environment (--shell bash|fish|powershell)
  doctor    Check sign-in, models and settings; --probe tests prefill per model

Flags (each also reads COB_<FLAG> from the environment):
  --port <n>              Port to listen on (4141)
  --host <addr>           Address to bind (127.0.0.1); other addresses need --allow-remote
  --account-type <type>   individual, business or enterprise (individual)
  --model <id>            Main model slot (newest Opus on the account)
  --small-model <id>      Helper model slot (claude-haiku-4.5 if available)
  --prefill <mode>        auto, convert, drop or off (auto)
  --alias FROM=TO         Map a model ID Claude Code sends to a Copilot ID (repeatable)
  --strict-models         Reject unknown model IDs instead of using the matching slot
  -v, --verbose           One line per request; -vv adds debug output
  --log-bodies <dir>      Write each request and response to files in <dir>
  --rate-limit <s>        Minimum seconds between upstream calls; --wait queues instead of 429
  --github-token <t>      Use this GitHub token instead of the saved one (prefer COB_GITHUB_TOKEN)
  --api-key <key>         Key clients must send (default: random, saved; prefer COB_API_KEY)
  -h, --help              Show this help

Config file: ~/.config/co-claude-bridge/config.json (same keys, camelCase, plus "modelAliases").
Unofficial: this uses reverse-engineered Copilot endpoints. Check GitHub's terms first.
`;

/**
 * Run one command. Returns the exit code, or -1 when the server keeps running.
 * Kept apart from cli.ts so tests can call it in-process.
 */
export async function main(argv: string[]): Promise<number> {
  let cfg;
  try {
    cfg = loadConfig(argv);
  } catch (e) {
    if (e instanceof ConfigError) {
      process.stderr.write(`co-claude-bridge: ${e.message}\n`);
      return 2;
    }
    throw e;
  }
  if (cfg.version) {
    console.log(VERSION);
    return 0;
  }
  if (cfg.help) {
    console.log(HELP.trimEnd());
    return 0;
  }
  setLevel(cfg.verbose);
  for (const w of cfg.warnings) log.warn(w);

  switch (cfg.command) {
    case "auth":
      await auth();
      return 0;
    case "logout":
      await logout();
      return 0;
    case "models":
      await models(cfg);
      return 0;
    case "doctor":
      return doctor(cfg);
    case "env": {
      const ctx = await buildContext(cfg, { allowLogin: true });
      await ctx.bridge.init();
      console.log(formatEnv(claudeEnv(cfg, ctx.bridge.slots, ctx.apiKey), cfg.shell));
      return 0;
    }
    default: {
      const stop = await start(cfg);
      const shutdown = () => {
        log.info("Shutting down");
        void stop().then(() => process.exit(0));
      };
      process.once("SIGINT", shutdown);
      process.once("SIGTERM", shutdown);
      return -1; // keep running
    }
  }
}
