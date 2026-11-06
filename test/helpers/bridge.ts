import fs from "node:fs";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";

import { buildContext, type Context } from "../../src/commands/context.ts";
import { loadConfig } from "../../src/config.ts";
import { setSink } from "../../src/log.ts";
import { createServer } from "../../src/server/http.ts";
import type { MockCopilot } from "./mock-copilot.ts";

export const logLines: string[] = [];
setSink((l) => logLines.push(l.replace(/\x1b\[[0-9;]*m/g, "")));

export interface Running {
  url: string;
  ctx: Context;
  dataDir: string;
  close(): Promise<void>;
}

export async function startBridge(mock: MockCopilot, args: string[] = []): Promise<Running> {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cob-test-"));
  process.env.COB_DATA_DIR = dataDir;
  const cfg = loadConfig(
    ["--copilot-token", "test-token", "--copilot-base-url", mock.url, "--port", "0", ...args],
    {},
    path.join(dataDir, "no-config.json"),
  );
  const ctx = await buildContext(cfg, { allowLogin: false });
  await ctx.bridge.init();
  const server = createServer(ctx.bridge);
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  return {
    url,
    ctx,
    dataDir,
    close: () =>
      new Promise<void>((r) => {
        server.closeAllConnections();
        server.close(() => r());
      }),
  };
}

export async function post(url: string, body: unknown): Promise<{ status: number; json: any; text: string }> {
  const res = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const text = await res.text();
  let json: any;
  try {
    json = JSON.parse(text);
  } catch {
    json = undefined;
  }
  return { status: res.status, json, text };
}

/** Parse an Anthropic SSE body into its event objects. */
export function events(text: string): any[] {
  return text
    .split("\n\n")
    .map((b) => b.split("\n").find((l) => l.startsWith("data: ")))
    .filter(Boolean)
    .map((l) => JSON.parse(l!.slice(6)));
}

/** Rebuild the final message text and tool calls from stream events. */
export function assemble(evs: any[]): { text: string; tools: Array<{ name: string; input: unknown }>; stop: string } {
  const blocks: any[] = [];
  let stop = "";
  for (const e of evs) {
    if (e.type === "content_block_start") blocks[e.index] = { ...e.content_block, json: "" };
    if (e.type === "content_block_delta" && e.delta.type === "text_delta") blocks[e.index].text += e.delta.text;
    if (e.type === "content_block_delta" && e.delta.type === "input_json_delta") blocks[e.index].json += e.delta.partial_json;
    if (e.type === "message_delta") stop = e.delta.stop_reason;
  }
  return {
    text: blocks.filter((b) => b.type === "text").map((b) => b.text).join(""),
    tools: blocks.filter((b) => b.type === "tool_use").map((b) => ({ name: b.name, input: JSON.parse(b.json || "{}") })),
    stop,
  };
}
