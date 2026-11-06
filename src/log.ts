// Small leveled logger. Level 0: normal, 1: --verbose (one line per request), 2: -vv (debug).

const useColor = Boolean(process.stderr.isTTY) && !process.env.NO_COLOR;
const paint = (code: string, s: string) => (useColor ? `\x1b[${code}m${s}\x1b[0m` : s);
export const color = {
  dim: (s: string) => paint("2", s),
  red: (s: string) => paint("31", s),
  green: (s: string) => paint("32", s),
  yellow: (s: string) => paint("33", s),
  cyan: (s: string) => paint("36", s),
  bold: (s: string) => paint("1", s),
};

let level = 0;
let sink: (line: string) => void = (line) => process.stderr.write(line + "\n");

export function setLevel(n: number): void {
  level = n;
}
export function getLevel(): number {
  return level;
}
/** Tests capture output by swapping the sink. */
export function setSink(fn: (line: string) => void): void {
  sink = fn;
}

function stamp(): string {
  return color.dim(new Date().toTimeString().slice(0, 8));
}

function emit(tag: string, args: unknown[]): void {
  const text = args.map((a) => (typeof a === "string" ? a : a instanceof Error ? a.message : JSON.stringify(a))).join(" ");
  sink(`${stamp()} ${tag}${text}`);
}

export const log = {
  info: (...a: unknown[]) => emit("", a),
  warn: (...a: unknown[]) => emit(color.yellow("warn "), a),
  error: (...a: unknown[]) => emit(color.red("error "), a),
  verbose: (...a: unknown[]) => {
    if (level >= 1) emit("", a);
  },
  debug: (...a: unknown[]) => {
    if (level >= 2) emit(color.dim("debug "), a);
  },
};

/** Keep the first 4 and last 4 characters of a secret. */
export function mask(secret: string | undefined): string {
  if (!secret) return "(none)";
  if (secret.length <= 12) return "****";
  return `${secret.slice(0, 4)}…${secret.slice(-4)}`;
}
