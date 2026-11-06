// OpenAI Chat Completions response -> Anthropic Messages response.

import type { MessagesResponse, StopReason, TextBlock, ToolUseBlock, Usage } from "../types/anthropic.ts";
import type { ChatResponse, ChatUsage, FinishReason } from "../types/openai.ts";
import { PrefixStripper } from "./prefix-strip.ts";

export function mapStopReason(reason: FinishReason | undefined, hasToolUse: boolean): StopReason {
  if (hasToolUse) return "tool_use";
  switch (reason) {
    case "length":
      return "max_tokens";
    case "tool_calls":
    case "function_call":
      return "tool_use";
    case "content_filter":
      return "refusal";
    default:
      return "end_turn";
  }
}

export function mapUsage(u: ChatUsage | null | undefined): Usage {
  const cached = u?.prompt_tokens_details?.cached_tokens ?? 0;
  const usage: Usage = {
    input_tokens: Math.max(0, (u?.prompt_tokens ?? 0) - cached),
    output_tokens: u?.completion_tokens ?? 0,
  };
  if (cached) usage.cache_read_input_tokens = cached;
  return usage;
}

/** Parse tool-call arguments; a model occasionally emits invalid JSON. */
export function parseArguments(raw: string | undefined): unknown {
  if (!raw || !raw.trim()) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return { _raw_arguments: raw };
  }
}

export function messageId(upstreamId: string | undefined): string {
  const base = (upstreamId ?? "").replace(/[^A-Za-z0-9_]/g, "") || Math.random().toString(36).slice(2);
  return base.startsWith("msg_") ? base : `msg_${base}`;
}

export function fromOpenAI(res: ChatResponse, model: string, prefillText?: string): MessagesResponse {
  const text: TextBlock[] = [];
  const tools: ToolUseBlock[] = [];
  let finish: FinishReason | undefined;
  // Copilot sometimes splits one reply across choices (text in one, tool calls in another).
  for (const choice of res.choices ?? []) {
    const c = choice.message?.content;
    if (typeof c === "string" && c) text.push({ type: "text", text: c });
    for (const call of choice.message?.tool_calls ?? []) {
      tools.push({ type: "tool_use", id: call.id, name: call.function.name, input: parseArguments(call.function.arguments) });
    }
    if (choice.finish_reason && (!finish || choice.finish_reason === "tool_calls")) finish = choice.finish_reason;
  }

  if (prefillText && text.length) {
    const s = new PrefixStripper(prefillText);
    const first = text[0]!;
    first.text = s.push(first.text) + s.flush();
    if (!first.text) text.shift();
  }

  return {
    id: messageId(res.id),
    type: "message",
    role: "assistant",
    model,
    content: [...text, ...tools],
    stop_reason: mapStopReason(finish, tools.length > 0),
    stop_sequence: null,
    usage: mapUsage(res.usage),
  };
}
