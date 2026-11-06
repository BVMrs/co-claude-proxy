import type { MessagesRequest } from "../types/anthropic.ts";

const CHARS_PER_TOKEN = 3.5;
const TOOL_OVERHEAD = 346; // Anthropic's documented tool-use system prompt overhead

/**
 * Rough input token estimate for /v1/messages/count_tokens. Claude Code uses
 * it for its context meter; exact counts would need Anthropic's tokenizer.
 */
export function estimateTokens(req: MessagesRequest): number {
  let chars = 0;
  const add = (v: unknown) => {
    if (typeof v === "string") chars += v.length;
    else if (Array.isArray(v)) v.forEach(add);
    else if (v && typeof v === "object") {
      const o = v as Record<string, unknown>;
      if (o.type === "image") {
        chars += 1600 * CHARS_PER_TOKEN; // ~1.6k tokens for a typical screenshot
        return;
      }
      for (const [k, val] of Object.entries(o)) if (k !== "cache_control" && k !== "type") add(val);
    }
  };
  add(req.system);
  add(req.messages);
  let tokens = Math.ceil(chars / CHARS_PER_TOKEN);
  if (req.tools?.length) tokens += TOOL_OVERHEAD + Math.ceil(JSON.stringify(req.tools).length / CHARS_PER_TOKEN);
  return Math.max(1, tokens);
}
