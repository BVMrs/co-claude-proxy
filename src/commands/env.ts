import type { Config } from "../config.ts";
import type { Slots } from "../pipeline/resolve-model.ts";

/** The environment Claude Code needs to talk to the bridge, for the chosen shell. */
export function claudeEnv(cfg: Config, slots: Slots, apiKey: string, port = cfg.port): Array<[string, string]> {
  const host = cfg.host === "0.0.0.0" || cfg.host === "::" ? "127.0.0.1" : cfg.host;
  const vars: Array<[string, string]> = [
    ["ANTHROPIC_BASE_URL", `http://${host.includes(":") ? `[${host}]` : host}:${port}`],
    ["ANTHROPIC_AUTH_TOKEN", apiKey],
  ];
  if (slots.main) vars.push(["ANTHROPIC_MODEL", slots.main]);
  if (slots.opus) vars.push(["ANTHROPIC_DEFAULT_OPUS_MODEL", slots.opus]);
  if (slots.sonnet) vars.push(["ANTHROPIC_DEFAULT_SONNET_MODEL", slots.sonnet]);
  if (slots.small) {
    vars.push(["ANTHROPIC_DEFAULT_HAIKU_MODEL", slots.small]);
    vars.push(["ANTHROPIC_SMALL_FAST_MODEL", slots.small]);
  }
  vars.push(["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1"]);
  return vars;
}

const UNSET = ["CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_API_KEY"];

export function formatEnv(vars: Array<[string, string]>, shell: Config["shell"]): string {
  const q = (v: string) => `'${v.replace(/'/g, `'\\''`)}'`;
  switch (shell) {
    case "fish":
      return [...vars.map(([k, v]) => `set -gx ${k} ${q(v)}`), ...UNSET.map((k) => `set -e ${k}`)].join("\n");
    case "powershell":
      return [
        ...vars.map(([k, v]) => `$env:${k} = "${v.replace(/"/g, '`"')}"`),
        ...UNSET.map((k) => `Remove-Item Env:${k} -ErrorAction SilentlyContinue`),
      ].join("\n");
    default:
      return [...vars.map(([k, v]) => `export ${k}=${q(v)}`), `unset ${UNSET.join(" ")}`].join("\n");
  }
}

/** Environment settings that send Claude Code somewhere other than the bridge. */
export function envConflicts(env: NodeJS.ProcessEnv = process.env): string[] {
  const out: string[] = [];
  for (const k of ["CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"]) {
    if (env[k] && env[k] !== "0") out.push(`${k} is set, so Claude Code will go to ${k.includes("BEDROCK") ? "Bedrock" : "Vertex"} instead of the bridge`);
  }
  if (env.ANTHROPIC_API_KEY) out.push("ANTHROPIC_API_KEY is set; with ANTHROPIC_BASE_URL it's harmless, but unset it to avoid confusion");
  return out;
}
