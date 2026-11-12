import type { AddressInfo } from "node:net";

import type { Config } from "../config.ts";
import { color, log } from "../log.ts";
import { createServer } from "../server/http.ts";
import { buildContext } from "./context.ts";
import { claudeEnv, envConflicts, formatEnv } from "./env.ts";

export async function start(cfg: Config): Promise<() => Promise<void>> {
  const ctx = await buildContext(cfg, { allowLogin: true });
  if (ctx.login) log.info(`Signed in to GitHub as ${color.bold(ctx.login)}`);
  await ctx.bridge.init();
  const ids = ctx.registry.ids();
  const s = ctx.bridge.slots;
  log.info(`${ids.length} models available. Main: ${s.main}, helpers: ${s.small}. Prefill repair: ${cfg.prefill}`);

  const server = createServer(ctx.bridge, { apiKey: ctx.apiKey, allowRemote: cfg.allowRemote });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(cfg.port, cfg.host, () => resolve());
  });
  const port = (server.address() as AddressInfo).port;
  log.info(`co-claude-bridge listening on ${color.cyan(`http://${cfg.host}:${port}`)}`);
  for (const c of envConflicts()) log.warn(c);
  process.stderr.write(
    `\nIn another terminal, start Claude Code with:\n\n${formatEnv(claudeEnv(cfg, s, ctx.apiKey, port), cfg.shell)}\nclaude\n\n`,
  );
  log.info(color.dim("This uses unofficial Copilot endpoints; check GitHub's terms and your employer's policy."));

  return () =>
    new Promise<void>((resolve) => {
      server.closeAllConnections();
      server.close(() => resolve());
    });
}
