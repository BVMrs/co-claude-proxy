import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { Config } from "../config.ts";
import { color } from "../log.ts";
import { isModelNotSupported, isPrefillError, upstreamMessage } from "../pipeline/errors.ts";
import { parseClaudeId } from "../pipeline/resolve-model.ts";
import { buildContext } from "./context.ts";
import { envConflicts } from "./env.ts";

const ok = (s: string) => console.log(`${color.green("✓")} ${s}`);
const bad = (s: string) => console.log(`${color.red("✗")} ${s}`);
const note = (s: string) => console.log(`${color.yellow("!")} ${s}`);

export async function doctor(cfg: Config): Promise<number> {
  let failures = 0;

  const conflicts = [...envConflicts(), ...settingsConflicts()];
  if (conflicts.length) conflicts.forEach(note);
  else ok("No environment or settings that would send Claude Code to Bedrock or Vertex");

  let ctx;
  try {
    ctx = await buildContext(cfg, { allowLogin: false });
    ok(`Copilot token issued${ctx.login ? ` for ${ctx.login}` : ""}`);
  } catch (e) {
    bad((e as Error).message);
    return 1;
  }

  try {
    const list = await ctx.registry.list();
    ok(`Model list loaded: ${list.length} chat models from ${ctx.client.baseUrl()}`);
  } catch (e) {
    bad(`Couldn't load /models: ${(e as Error).message}`);
    return 1;
  }

  try {
    await ctx.bridge.init();
    const s = ctx.bridge.slots;
    ok(`Model slots exist: main ${s.main}, helpers ${s.small}`);
  } catch (e) {
    bad((e as Error).message);
    failures++;
  }

  const claude = ctx.registry.ids().filter((id) => parseClaudeId(id));
  if (!cfg.probe) {
    note(`Skipped the live prefill probe on ${claude.length} Claude models. Add --probe to run it (about 2 premium requests per model).`);
    return failures ? 1 : 0;
  }

  console.log(`\nProbing ${claude.length} Claude models (max_tokens 8):`);
  for (const id of claude.sort()) {
    const base = [{ role: "user" as const, content: "Reply with one word: ok" }];
    const baseline = await ctx.client.chat({ model: id, messages: base, max_tokens: 8 });
    const baseText = await baseline.text();
    if (!baseline.ok) {
      bad(`${id}: unusable (${baseline.status} ${isModelNotSupported(baseText) ? "model_not_supported" : upstreamMessage(baseText).message.slice(0, 80)})`);
      continue;
    }
    const pre = await ctx.client.chat({
      model: id,
      messages: [...base, { role: "assistant", content: "The word is" }],
      max_tokens: 8,
    });
    const preText = await pre.text();
    if (pre.ok) {
      ctx.bridge.recordPrefillVerdict(id, false);
      ok(`${id}: accepts prefill`);
    } else if (pre.status === 400 && isPrefillError(preText)) {
      ctx.bridge.recordPrefillVerdict(id, true);
      ok(`${id}: rejects prefill (the bridge repairs these requests)`);
    } else {
      bad(`${id}: prefill probe returned ${pre.status} ${upstreamMessage(preText).message.slice(0, 80)}`);
      failures++;
    }
  }
  console.log(`\nSaved the results; \`start\` uses them from now on.`);
  return failures ? 1 : 0;
}

/** CLAUDE_CODE_USE_BEDROCK/VERTEX set under "env" in Claude Code settings files. */
function settingsConflicts(): string[] {
  const files = [
    path.join(os.homedir(), ".claude", "settings.json"),
    path.join(process.cwd(), ".claude", "settings.json"),
    path.join(process.cwd(), ".claude", "settings.local.json"),
  ];
  const out: string[] = [];
  for (const f of files) {
    try {
      const env = (JSON.parse(fs.readFileSync(f, "utf8")) as { env?: Record<string, string> }).env ?? {};
      for (const k of ["CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_BASE_URL"]) {
        if (env[k]) out.push(`${f} sets ${k}=${env[k]} under "env", which overrides your shell`);
      }
    } catch {
      // missing or unreadable: nothing to report
    }
  }
  return out;
}
