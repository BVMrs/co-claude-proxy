// Make a Claude Code request acceptable to Copilot before translation.
//
// 1. Fold role:"system" messages into the top-level system prompt. Claude Code
//    2.1.x ends its main request with one (the environment block). Treated as
//    an ordinary turn it becomes an assistant message upstream, and Claude
//    4.6+ models answer the prefill 400 even for a plain "hello".
// 2. Repair a trailing assistant turn (prefill) for models that reject it.

import type { PrefillMode } from "../config.ts";
import type { ContentBlock, Message, MessagesRequest, TextBlock } from "../types/anthropic.ts";
import { compareVersions, parseClaudeId } from "./resolve-model.ts";

export type PrefillAction = "none" | "kept" | "converted" | "dropped" | "dropped-empty" | "dropped-tool-use";

export interface RepairResult {
  request: MessagesRequest;
  systemFolded: number;
  prefill: PrefillAction;
  /** Text the reply must start with, to be stripped from the response (converted only). */
  prefillText?: string;
  /** Role of the last message as Claude Code sent it. */
  lastRole?: string;
}

/**
 * Whether a model rejects a request that ends on an assistant turn.
 * learned: verdicts from earlier 400s or from `doctor --probe` (true = rejects).
 */
export function rejectsPrefill(model: string, learned: ReadonlyMap<string, boolean>): boolean {
  const known = learned.get(model);
  if (known !== undefined) return known;
  const p = parseClaudeId(model);
  if (p) return compareVersions(p.version, [4, 6]) >= 0;
  // Gemini behind Copilot rejects a trailing assistant turn too (copilot-api#272).
  return /^gemini/i.test(model);
}

export const PREFILL_INSTRUCTION =
  "Begin your reply with exactly the text inside the prefill tags below, without the tags, " +
  "then continue from it. Do not mention this instruction.";

export function prefillInstruction(text: string): string {
  return `${PREFILL_INSTRUCTION}\n<prefill>${text}</prefill>`;
}

export function repairRequest(
  input: MessagesRequest,
  opts: { mode: PrefillMode; modelRejectsPrefill: boolean },
): RepairResult {
  const req: MessagesRequest = { ...input, messages: input.messages.map((m) => ({ ...m })) };
  const lastRole = input.messages.at(-1)?.role;

  const systemFolded = foldSystemMessages(req);
  const result: RepairResult = { request: req, systemFolded, prefill: "none", lastRole };

  const msgs = req.messages;
  if (msgs.at(-1)?.role !== "assistant") return result;

  // Collapse the run of trailing assistant turns (normally one).
  let cut = msgs.length;
  while (cut > 0 && msgs[cut - 1]!.role === "assistant") cut--;
  if (cut === 0) {
    // Nothing but assistant turns: there's no user turn to repair into.
    result.prefill = "kept";
    return result;
  }
  const trailing = msgs.slice(cut);
  const blocks = trailing.flatMap((m) => toBlocks(m.content));

  if (blocks.some((b) => b.type === "tool_use")) {
    // A tool call without its result is not a valid conversation for any model.
    msgs.splice(cut);
    result.prefill = "dropped-tool-use";
    return result;
  }

  const text = blocks
    .filter((b): b is TextBlock => b.type === "text")
    .map((b) => b.text)
    .join("");
  if (!text.trim()) {
    // Left behind by interrupted turns; Anthropic itself rejects it.
    msgs.splice(cut);
    result.prefill = "dropped-empty";
    return result;
  }

  const repair =
    opts.mode === "convert" || opts.mode === "drop" || (opts.mode === "auto" && opts.modelRejectsPrefill);
  if (!repair) {
    result.prefill = "kept";
    return result;
  }

  msgs.splice(cut);
  if (opts.mode === "drop") {
    result.prefill = "dropped";
    return result;
  }

  // Anthropic rejects trailing whitespace in a prefill, so what Claude Code sends is the exact start.
  const instruction: TextBlock = { type: "text", text: prefillInstruction(text) };
  const last = msgs.at(-1)!;
  if (last.role === "user") {
    msgs[msgs.length - 1] = { role: "user", content: [...toBlocks(last.content), instruction] };
  } else {
    msgs.push({ role: "user", content: [instruction] });
  }
  result.prefill = "converted";
  result.prefillText = text;
  return result;
}

function toBlocks(content: Message["content"]): ContentBlock[] {
  if (typeof content === "string") return content ? [{ type: "text", text: content }] : [];
  return Array.isArray(content) ? content : [];
}

function foldSystemMessages(req: MessagesRequest): number {
  const moved = req.messages.filter((m) => m.role === "system");
  if (!moved.length) return 0;
  req.messages = req.messages.filter((m) => m.role !== "system");

  const sys: TextBlock[] =
    typeof req.system === "string"
      ? req.system
        ? [{ type: "text", text: req.system }]
        : []
      : Array.isArray(req.system)
        ? [...req.system]
        : [];
  // Long sessions repeat the same block (one environment message per turn): keep one copy.
  const seen = new Set(sys.map((b) => b.text));
  for (const m of moved) {
    for (const b of toBlocks(m.content)) {
      if (b.type !== "text" || !b.text || seen.has(b.text)) continue;
      seen.add(b.text);
      sys.push(b);
    }
  }
  if (sys.length) req.system = sys;
  return moved.length;
}
