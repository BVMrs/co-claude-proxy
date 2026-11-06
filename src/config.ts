import fs from "node:fs";

import { configPath } from "./paths.ts";
import { isLoopbackHost } from "./security/upstream.ts";

export type PrefillMode = "auto" | "convert" | "drop" | "off";
export type AccountType = "individual" | "business" | "enterprise";

export interface Config {
  command: string;
  port: number;
  host: string;
  accountType: AccountType;
  githubToken?: string;
  /** Skip GitHub entirely and use this Copilot token (tests, advanced use). */
  copilotToken?: string;
  /** Override the Copilot API base URL (tests, proxies). */
  copilotBaseUrl?: string;
  /** Key clients must send to the local server. Default: a random key saved in the data directory. */
  apiKey?: string;
  /** Let --host bind a non-loopback address. Off by default. */
  allowRemote: boolean;
  model?: string;
  smallModel?: string;
  prefill: PrefillMode;
  strictModels: boolean;
  verbose: number;
  logBodies?: string;
  rateLimit?: number;
  wait: boolean;
  modelAliases: Record<string, string>;
  shell: "bash" | "fish" | "powershell";
  probe: boolean;
  help: boolean;
  version: boolean;
  /** Problems worth telling the user about that don't stop the run. */
  warnings: string[];
}

export class ConfigError extends Error {}

interface FlagSpec {
  key: keyof Config;
  type: "string" | "number" | "boolean";
  env?: string;
}

// Flag name -> config key. Every flag also reads COB_<NAME> from the environment.
const FLAGS: Record<string, FlagSpec> = {
  port: { key: "port", type: "number" },
  host: { key: "host", type: "string" },
  "account-type": { key: "accountType", type: "string" },
  "github-token": { key: "githubToken", type: "string" },
  "copilot-token": { key: "copilotToken", type: "string" },
  "copilot-base-url": { key: "copilotBaseUrl", type: "string" },
  "api-key": { key: "apiKey", type: "string" },
  "allow-remote": { key: "allowRemote", type: "boolean" },
  model: { key: "model", type: "string" },
  "small-model": { key: "smallModel", type: "string" },
  prefill: { key: "prefill", type: "string" },
  "strict-models": { key: "strictModels", type: "boolean" },
  "log-bodies": { key: "logBodies", type: "string" },
  "rate-limit": { key: "rateLimit", type: "number" },
  wait: { key: "wait", type: "boolean" },
  shell: { key: "shell", type: "string" },
  probe: { key: "probe", type: "boolean" },
  help: { key: "help", type: "boolean" },
  version: { key: "version", type: "boolean" },
};

const COMMANDS = new Set(["start", "auth", "logout", "models", "env", "doctor", "help"]);

function defaults(): Config {
  return {
    command: "start",
    port: 4141,
    host: "127.0.0.1",
    accountType: "individual",
    prefill: "auto",
    strictModels: false,
    verbose: 0,
    wait: false,
    modelAliases: {},
    shell: "bash",
    probe: false,
    help: false,
    version: false,
    allowRemote: false,
    warnings: [],
  };
}

/** Flags that carry a credential. On the command line they show up in `ps` and shell history. */
const SECRET_FLAGS = new Set(["github-token", "copilot-token", "api-key"]);

function coerce(spec: FlagSpec, name: string, raw: string | boolean): unknown {
  if (spec.type === "boolean") {
    if (typeof raw === "boolean") return raw;
    if (/^(1|true|yes|on)$/i.test(raw)) return true;
    if (/^(0|false|no|off|)$/i.test(raw)) return false;
    throw new ConfigError(`--${name} expects true or false, got "${raw}"`);
  }
  if (typeof raw === "boolean") throw new ConfigError(`--${name} needs a value`);
  if (spec.type === "number") {
    const n = Number(raw);
    if (!Number.isFinite(n)) throw new ConfigError(`--${name} expects a number, got "${raw}"`);
    return n;
  }
  return raw;
}

function envName(flag: string): string {
  return "COB_" + flag.toUpperCase().replace(/-/g, "_");
}

function readConfigFile(file: string): Partial<Config> {
  let text: string;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return {};
  }
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch (e) {
    throw new ConfigError(`${file} is not valid JSON: ${(e as Error).message}`);
  }
  if (!data || typeof data !== "object") throw new ConfigError(`${file} must hold a JSON object`);
  const out: Record<string, unknown> = {};
  const byKey = new Map(Object.entries(FLAGS).map(([flag, spec]) => [spec.key as string, { flag, spec }]));
  for (const [k, v] of Object.entries(data as Record<string, unknown>)) {
    if (k === "modelAliases") {
      if (!v || typeof v !== "object" || Array.isArray(v)) throw new ConfigError(`${file}: modelAliases must be an object`);
      out.modelAliases = Object.fromEntries(Object.entries(v as Record<string, unknown>).map(([a, b]) => [a, String(b)]));
      continue;
    }
    const entry = byKey.get(k);
    if (!entry) throw new ConfigError(`${file}: unknown key "${k}"`);
    out[k] = coerce(entry.spec, entry.flag, typeof v === "boolean" ? v : String(v));
  }
  return out as Partial<Config>;
}

/** Precedence: flags > COB_* environment variables > config file > defaults. */
export function loadConfig(argv: string[], env: NodeJS.ProcessEnv = process.env, file = configPath()): Config {
  const cfg = defaults();
  const configFile = env.COB_CONFIG ?? file;
  const fromFile = readConfigFile(configFile);
  Object.assign(cfg, fromFile);
  for (const k of ["githubToken", "copilotToken", "apiKey"] as const) {
    if (fromFile[k]) cfg.warnings.push(`${configFile} holds ${k}; keep credentials out of config files (use the saved sign-in or an environment variable)`);
  }

  for (const [flag, spec] of Object.entries(FLAGS)) {
    const v = env[envName(flag)];
    if (v !== undefined && v !== "") (cfg as unknown as Record<string, unknown>)[spec.key] = coerce(spec, flag, v);
  }
  if (env.COB_VERBOSE) cfg.verbose = Number(env.COB_VERBOSE) || 1;

  const positional: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]!;
    if (arg === "-v" || arg === "--verbose") {
      cfg.verbose = Math.max(cfg.verbose, 1);
      continue;
    }
    if (arg === "-vv") {
      cfg.verbose = 2;
      continue;
    }
    if (arg === "-h") {
      cfg.help = true;
      continue;
    }
    if (arg === "--alias") {
      const pair = argv[++i];
      const [from, to] = (pair ?? "").split("=");
      if (!from || !to) throw new ConfigError(`--alias expects FROM=TO, got "${pair ?? ""}"`);
      cfg.modelAliases[from] = to;
      continue;
    }
    if (arg.startsWith("--")) {
      let name = arg.slice(2);
      let value: string | boolean | undefined;
      const eq = name.indexOf("=");
      if (eq >= 0) {
        value = name.slice(eq + 1);
        name = name.slice(0, eq);
      }
      let negate = false;
      if (!FLAGS[name] && name.startsWith("no-") && FLAGS[name.slice(3)]?.type === "boolean") {
        negate = true;
        name = name.slice(3);
      }
      const spec = FLAGS[name];
      if (!spec) throw new ConfigError(`unknown flag --${name} (see --help)`);
      if (SECRET_FLAGS.has(name)) {
        cfg.warnings.push(`--${name} on the command line is visible to other programs on this machine; prefer ${envName(name)}`);
      }
      if (value === undefined) {
        if (spec.type === "boolean") value = !negate;
        else {
          const next = argv[++i];
          if (next === undefined) throw new ConfigError(`--${name} needs a value`);
          value = next;
        }
      }
      (cfg as unknown as Record<string, unknown>)[spec.key] = coerce(spec, name, value);
      continue;
    }
    positional.push(arg);
  }

  if (positional.length > 1) throw new ConfigError(`unexpected argument "${positional[1]}"`);
  if (positional[0]) {
    if (!COMMANDS.has(positional[0])) throw new ConfigError(`unknown command "${positional[0]}" (see --help)`);
    cfg.command = positional[0] === "help" ? "start" : positional[0];
    if (positional[0] === "help") cfg.help = true;
  }

  if (!["auto", "convert", "drop", "off"].includes(cfg.prefill)) {
    throw new ConfigError(`--prefill must be auto, convert, drop or off, got "${cfg.prefill}"`);
  }
  if (!["individual", "business", "enterprise"].includes(cfg.accountType)) {
    throw new ConfigError(`--account-type must be individual, business or enterprise, got "${cfg.accountType}"`);
  }
  if (!["bash", "fish", "powershell"].includes(cfg.shell)) {
    throw new ConfigError(`--shell must be bash, fish or powershell, got "${cfg.shell}"`);
  }
  if (!Number.isInteger(cfg.port) || cfg.port < 0 || cfg.port > 65535) throw new ConfigError(`--port must be 0-65535`);
  if (!isLoopbackHost(cfg.host)) {
    if (!cfg.allowRemote) {
      throw new ConfigError(
        `--host ${cfg.host} would expose the bridge, and your Copilot subscription, to the network. ` +
          `Add --allow-remote if you really mean it.`,
      );
    }
    cfg.warnings.push(`listening on ${cfg.host}: anyone who can reach it and has the API key can use your Copilot subscription, and traffic is not encrypted`);
  }
  if (cfg.apiKey !== undefined && cfg.apiKey.length < 16) throw new ConfigError(`--api-key must be at least 16 characters`);
  return cfg;
}
