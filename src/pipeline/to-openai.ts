// Anthropic Messages request -> OpenAI Chat Completions request (Copilot's format).

import type { Initiator } from "../copilot/headers.ts";
import type {
  ContentBlock,
  ImageBlock,
  Message,
  MessagesRequest,
  TextBlock,
  Tool,
  ToolChoice,
  ToolResultBlock,
} from "../types/anthropic.ts";
import type { ChatMessage, ChatRequest, ChatTool, ChatToolChoice, ContentPart, ToolCall } from "../types/openai.ts";

export interface Translated {
  payload: ChatRequest;
  vision: boolean;
  initiator: Initiator;
  /** Tools dropped because they aren't plain function tools (e.g. Anthropic server tools). */
  droppedTools: string[];
}

export function toOpenAI(req: MessagesRequest, model: string): Translated {
  const messages: ChatMessage[] = [];
  const system = systemText(req.system);
  if (system) messages.push({ role: "system", content: system });
  for (const m of req.messages) messages.push(...translateMessage(m));

  const { tools, dropped } = translateTools(req.tools);
  const payload: ChatRequest = {
    model,
    messages,
    max_tokens: req.max_tokens,
    stream: Boolean(req.stream),
  };
  if (req.stream) payload.stream_options = { include_usage: true };
  if (req.temperature !== undefined) payload.temperature = req.temperature;
  if (req.top_p !== undefined) payload.top_p = req.top_p;
  if (req.stop_sequences?.length) payload.stop = req.stop_sequences;
  if (tools.length) {
    payload.tools = tools;
    const choice = translateToolChoice(req.tool_choice);
    if (choice) payload.tool_choice = choice;
    if (req.tool_choice && "disable_parallel_tool_use" in req.tool_choice && req.tool_choice.disable_parallel_tool_use) {
      payload.parallel_tool_calls = false;
    }
  }

  const vision = messages.some((m) => Array.isArray(m.content) && m.content.some((p) => p.type === "image_url"));
  return { payload, vision, initiator: initiatorOf(req), droppedTools: dropped };
}

/**
 * Copilot counts premium requests by X-Initiator. A turn the person typed is
 * "user"; a follow-up that only carries tool results is "agent". This matches
 * what VS Code's agent mode sends.
 */
export function initiatorOf(req: MessagesRequest): Initiator {
  const last = req.messages.at(-1);
  if (!last || last.role !== "user" || typeof last.content === "string") return "user";
  const blocks = last.content;
  const onlyToolResults = blocks.length > 0 && blocks.every((b) => b.type === "tool_result" || isReminder(b));
  return onlyToolResults && blocks.some((b) => b.type === "tool_result") ? "agent" : "user";
}

function isReminder(b: ContentBlock): boolean {
  return b.type === "text" && /^\s*<system-reminder>/.test(b.text);
}

function systemText(system: MessagesRequest["system"]): string {
  if (!system) return "";
  if (typeof system === "string") return system;
  return system
    .filter((b) => b.type === "text")
    .map((b) => b.text)
    .join("\n\n");
}

function blocksOf(content: Message["content"]): ContentBlock[] {
  return typeof content === "string" ? [{ type: "text", text: content }] : Array.isArray(content) ? content : [];
}

function translateMessage(m: Message): ChatMessage[] {
  if (m.role === "assistant") return [translateAssistant(blocksOf(m.content))];
  if (m.role === "system") return [{ role: "system", content: systemText(blocksOf(m.content) as TextBlock[]) }];
  return translateUser(m.content);
}

function translateUser(content: Message["content"]): ChatMessage[] {
  if (typeof content === "string") return [{ role: "user", content }];
  const out: ChatMessage[] = [];
  const parts: ContentPart[] = [];
  const toolImages: ContentPart[] = [];

  // Tool results must directly follow the assistant's tool calls, so they go first.
  for (const b of content) {
    if (b.type !== "tool_result") continue;
    const { text, images } = toolResultContent(b);
    out.push({ role: "tool", tool_call_id: b.tool_use_id, content: text });
    toolImages.push(...images);
  }
  if (toolImages.length) parts.push({ type: "text", text: "Images returned by the tool calls above:" }, ...toolImages);

  for (const b of content) {
    if (b.type === "text") parts.push({ type: "text", text: b.text });
    else if (b.type === "image") parts.push(imagePart(b));
    else if (b.type === "document") parts.push({ type: "text", text: "[A document was attached here; this bridge can't forward documents.]" });
  }
  if (parts.length) {
    const textOnly = parts.every((p) => p.type === "text");
    out.push({
      role: "user",
      content: textOnly ? (parts as Array<{ text: string }>).map((p) => p.text).join("\n\n") : parts,
    });
  }
  return out;
}

function toolResultContent(b: ToolResultBlock): { text: string; images: ContentPart[] } {
  const images: ContentPart[] = [];
  let text: string;
  if (typeof b.content === "string") text = b.content;
  else if (Array.isArray(b.content)) {
    const texts: string[] = [];
    for (const c of b.content) {
      if (c.type === "text") texts.push(c.text);
      else if (c.type === "image") {
        images.push(imagePart(c));
        texts.push("[image attached below]");
      }
    }
    text = texts.join("\n\n");
  } else text = "";
  if (b.is_error) text = `Error: ${text}`;
  return { text: text || "(no output)", images };
}

function imagePart(b: ImageBlock): ContentPart {
  const url = b.source.type === "base64" ? `data:${b.source.media_type};base64,${b.source.data}` : b.source.url;
  return { type: "image_url", image_url: { url } };
}

function translateAssistant(blocks: ContentBlock[]): ChatMessage {
  // Thinking blocks are the model's private reasoning; replaying them as text
  // would make the model treat them as things it said. Drop them.
  const text = blocks
    .filter((b): b is TextBlock => b.type === "text")
    .map((b) => b.text)
    .join("\n\n");
  const calls: ToolCall[] = blocks
    .filter((b) => b.type === "tool_use")
    .map((b) => ({
      id: b.id,
      type: "function",
      function: { name: b.name, arguments: JSON.stringify(b.input ?? {}) },
    }));
  if (calls.length) return { role: "assistant", content: text || null, tool_calls: calls };
  return { role: "assistant", content: text };
}

function translateTools(tools: Tool[] | undefined): { tools: ChatTool[]; dropped: string[] } {
  const out: ChatTool[] = [];
  const dropped: string[] = [];
  for (const t of tools ?? []) {
    if (!t.input_schema || (t.type && t.type !== "custom")) {
      dropped.push(t.name);
      continue;
    }
    out.push({
      type: "function",
      function: { name: t.name, description: t.description, parameters: t.input_schema },
    });
  }
  return { tools: out, dropped };
}

function translateToolChoice(choice: ToolChoice | undefined): ChatToolChoice | undefined {
  switch (choice?.type) {
    case "auto":
      return "auto";
    case "any":
      return "required";
    case "none":
      return "none";
    case "tool":
      return { type: "function", function: { name: choice.name } };
    default:
      return undefined;
  }
}
