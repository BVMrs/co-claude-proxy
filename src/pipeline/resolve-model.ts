// Map the model ID Claude Code sends onto one the Copilot account actually has.

export type ResolveHow = "exact" | "alias" | "normalized" | "slot";

export type Resolution =
  | { ok: true; id: string; how: ResolveHow }
  | { ok: false; message: string };

export interface ResolveOptions {
  aliases?: Record<string, string>;
  mainSlot?: string;
  opusSlot?: string;
  sonnetSlot?: string;
  smallSlot?: string;
  strict?: boolean;
}

export type Family = "opus" | "sonnet" | "haiku";

export interface ClaudeId {
  family: Family;
  /** e.g. [4, 5] for claude-sonnet-4.5; [3, 5] for claude-3.5-sonnet. */
  version: number[];
}

/** Parse claude-<family>-<x>[.<y>] and the older claude-<x>[.<y>]-<family> forms. */
export function parseClaudeId(id: string): ClaudeId | undefined {
  const s = id.toLowerCase();
  let m = /^claude-(opus|sonnet|haiku)-(\d+)(?:[.-](\d+))?\b/.exec(s);
  if (m) return { family: m[1] as Family, version: versionOf(m[2]!, m[3]) };
  m = /^claude-(\d+)(?:[.-](\d+))?-(opus|sonnet|haiku)\b/.exec(s);
  if (m) return { family: m[3] as Family, version: versionOf(m[1]!, m[2]) };
  return undefined;
}

function versionOf(major: string, minor?: string): number[] {
  // A trailing 8-digit date is not a minor version.
  if (minor !== undefined && minor.length < 4) return [Number(major), Number(minor)];
  return [Number(major)];
}

export function compareVersions(a: number[], b: number[]): number {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? 0) - (b[i] ?? 0);
    if (d) return d;
  }
  return 0;
}

/**
 * Candidate spellings of a requested ID, most specific first:
 * claude-sonnet-4-5-20250929[1m] -> claude-sonnet-4-5-20250929, claude-sonnet-4-5, claude-sonnet-4.5.
 */
export function normalizations(requested: string): string[] {
  const out: string[] = [];
  const add = (s: string) => {
    if (s && !out.includes(s)) out.push(s);
  };
  let s = requested.trim();
  s = s.replace(/^(anthropic|copilot|github)\//i, "");
  s = s.replace(/\[[^\]]*\]$/, ""); // [1m] context suffix
  add(s);
  s = s.replace(/-latest$/i, "");
  add(s);
  s = s.replace(/-\d{8}$/, ""); // dated snapshot
  add(s);
  add(s.toLowerCase());
  // Dashed minor version -> dotted: claude-opus-4-8 -> claude-opus-4.8, claude-3-5-sonnet -> claude-3.5-sonnet
  add(s.toLowerCase().replace(/^claude-(opus|sonnet|haiku)-(\d+)-(\d{1,2})$/, "claude-$1-$2.$3"));
  add(s.toLowerCase().replace(/^claude-(\d+)-(\d{1,2})-(opus|sonnet|haiku)$/, "claude-$1.$2-$3"));
  return out;
}

export function resolveModel(requested: string, available: readonly string[], opts: ResolveOptions = {}): Resolution {
  const have = new Set(available);
  if (have.has(requested)) return { ok: true, id: requested, how: "exact" };

  const alias = opts.aliases?.[requested];
  if (alias) {
    if (have.has(alias)) return { ok: true, id: alias, how: "alias" };
    return { ok: false, message: `Alias ${requested} -> ${alias} points at a model this account doesn't have. ${listHint(available)}` };
  }

  const lower = new Map(available.map((id) => [id.toLowerCase(), id]));
  for (const cand of normalizations(requested)) {
    const hit = have.has(cand) ? cand : lower.get(cand.toLowerCase());
    if (hit) return { ok: true, id: hit, how: "normalized" };
  }

  if (!opts.strict) {
    const fam = parseClaudeId(normalizations(requested).at(-1) ?? requested)?.family ?? familyWord(requested);
    const slot =
      fam === "haiku" ? opts.smallSlot : fam === "opus" ? (opts.opusSlot ?? opts.mainSlot) : fam === "sonnet" ? (opts.sonnetSlot ?? opts.mainSlot) : undefined;
    if (slot && have.has(slot)) return { ok: true, id: slot, how: "slot" };
  }

  return {
    ok: false,
    message: `Model "${requested}" isn't available on this Copilot account. ${listHint(available)}`,
  };
}

function familyWord(id: string): Family | undefined {
  const m = /(opus|sonnet|haiku)/i.exec(id);
  return m ? (m[1]!.toLowerCase() as Family) : undefined;
}

function listHint(available: readonly string[]): string {
  if (!available.length) return "The model list is empty; check that the account has Copilot chat access.";
  return `Use one of: ${[...available].sort().join(", ")}`;
}

/** Newest model of a family, by parsed version. Previews lose ties. */
export function newestOf(family: Family, available: readonly string[]): string | undefined {
  let best: { id: string; v: number[] } | undefined;
  for (const id of available) {
    const p = parseClaudeId(id);
    if (!p || p.family !== family) continue;
    if (!best || compareVersions(p.version, best.v) > 0) best = { id, v: p.version };
  }
  return best?.id;
}

export interface Slots {
  main?: string;
  opus?: string;
  sonnet?: string;
  small?: string;
}

/**
 * Default model slots from the account's list. The helper slot prefers
 * claude-haiku-4.5: it accepts prefill, so helper calls need no repair.
 */
export function pickSlots(available: readonly string[], prefer: { main?: string; small?: string } = {}): Slots {
  const opus = newestOf("opus", available);
  const sonnet = newestOf("sonnet", available);
  const haiku45 = available.find((id) => id === "claude-haiku-4.5");
  const main = prefer.main ?? opus ?? sonnet ?? available[0];
  const small = prefer.small ?? haiku45 ?? newestOf("haiku", available) ?? main;
  return { main, opus: opus ?? main, sonnet: sonnet ?? main, small };
}
