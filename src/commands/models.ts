import type { Config } from "../config.ts";
import { rejectsPrefill } from "../pipeline/repair.ts";
import { buildContext } from "./context.ts";

export async function models(cfg: Config): Promise<void> {
  const ctx = await buildContext(cfg, { allowLogin: true });
  const list = await ctx.registry.list();
  await ctx.bridge.init();
  const verdicts = ctx.bridge.prefillVerdicts;
  const rows = list.map((m) => {
    const known = verdicts.get(m.id);
    const prefill =
      known === undefined ? (rejectsPrefill(m.id, verdicts) ? "rejects?" : "accepts?") : known ? "rejects" : "accepts";
    const ctxTokens = m.capabilities?.limits?.max_context_window_tokens;
    return [
      m.id,
      m.vendor ?? "",
      ctxTokens ? `${Math.round(ctxTokens / 1000)}k` : "",
      m.capabilities?.supports?.tool_calls ? "yes" : "no",
      prefill,
    ];
  });
  rows.sort((a, b) => a[0]!.localeCompare(b[0]!));
  const head = ["MODEL", "VENDOR", "CONTEXT", "TOOLS", "PREFILL"];
  const widths = head.map((h, i) => Math.max(h.length, ...rows.map((r) => r[i]!.length)));
  const fmt = (r: string[]) => r.map((c, i) => c.padEnd(widths[i]!)).join("  ").trimEnd();
  console.log(fmt(head));
  for (const r of rows) console.log(fmt(r));
  console.log(`\n"?" means guessed from the version; run \`co-claude-bridge doctor --probe\` to test.`);
  const s = ctx.bridge.slots;
  console.log(`Default slots: main ${s.main}, opus ${s.opus}, sonnet ${s.sonnet}, helpers ${s.small}`);
}
