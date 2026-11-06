// OpenAI chat.completion.chunk stream -> Anthropic Messages SSE events.

import type { StreamEvent, Usage } from "../types/anthropic.ts";
import type { ChatChunk, FinishReason } from "../types/openai.ts";
import { mapStopReason, mapUsage, messageId } from "./from-openai.ts";
import { PrefixStripper } from "./prefix-strip.ts";

interface OpenBlock {
  index: number;
  kind: "text" | "tool";
}

export class StreamTranslator {
  private started = false;
  private finished = false;
  private nextIndex = 0;
  private open?: OpenBlock;
  /** "<choice>:<tool index>" -> Anthropic block index */
  private tools = new Map<string, { index: number; id: string }>();
  private finish?: FinishReason;
  private usage?: Usage;
  private readonly stripper?: PrefixStripper;
  private readonly model: string;
  textChars = 0;

  constructor(model: string, prefillText?: string) {
    this.model = model;
    if (prefillText) this.stripper = new PrefixStripper(prefillText);
  }

  /** The events that open the message; also emitted lazily by the first chunk. */
  start(upstreamId?: string): StreamEvent[] {
    if (this.started) return [];
    this.started = true;
    return [
      {
        type: "message_start",
        message: {
          id: messageId(upstreamId),
          type: "message",
          role: "assistant",
          model: this.model,
          content: [],
          stop_reason: null,
          stop_sequence: null,
          usage: { input_tokens: 0, output_tokens: 0 },
        },
      },
    ];
  }

  chunk(chunk: ChatChunk): StreamEvent[] {
    const events = this.start(chunk.id);
    if (chunk.usage) this.usage = mapUsage(chunk.usage);
    for (const choice of chunk.choices ?? []) {
      const ci = choice.index ?? 0;
      const delta = choice.delta ?? {};
      if (typeof delta.content === "string" && delta.content) {
        const text = this.stripper ? this.stripper.push(delta.content) : delta.content;
        if (text) events.push(...this.text(text));
      }
      for (const tc of delta.tool_calls ?? []) {
        const key = `${ci}:${tc.index ?? 0}`;
        let known = this.tools.get(key);
        if (tc.id && (!known || known.id !== tc.id)) {
          events.push(...this.flushStripper(), ...this.close());
          const index = this.nextIndex++;
          known = { index, id: tc.id };
          this.tools.set(key, known);
          this.open = { index, kind: "tool" };
          events.push({
            type: "content_block_start",
            index,
            content_block: { type: "tool_use", id: tc.id, name: tc.function?.name ?? "", input: {} },
          });
        }
        const args = tc.function?.arguments;
        // Deltas are addressed by index, so late arguments for an earlier call still land.
        if (known && args) {
          events.push({ type: "content_block_delta", index: known.index, delta: { type: "input_json_delta", partial_json: args } });
        }
      }
      if (choice.finish_reason && (!this.finish || choice.finish_reason === "tool_calls")) this.finish = choice.finish_reason;
    }
    return events;
  }

  /** End of the upstream stream ([DONE] or EOF). Usage chunks after finish_reason are already counted. */
  end(): StreamEvent[] {
    if (this.finished) return [];
    this.finished = true;
    const events = [...this.start(), ...this.flushStripper(), ...this.close()];
    events.push(
      {
        type: "message_delta",
        delta: { stop_reason: mapStopReason(this.finish, this.tools.size > 0), stop_sequence: null },
        usage: this.usage ?? { input_tokens: 0, output_tokens: 0 },
      },
      { type: "message_stop" },
    );
    return events;
  }

  usageSeen(): Usage | undefined {
    return this.usage;
  }

  private text(text: string): StreamEvent[] {
    const events: StreamEvent[] = [];
    if (this.open?.kind !== "text") {
      events.push(...this.close());
      const index = this.nextIndex++;
      this.open = { index, kind: "text" };
      events.push({ type: "content_block_start", index, content_block: { type: "text", text: "" } });
    }
    this.textChars += text.length;
    events.push({ type: "content_block_delta", index: this.open!.index, delta: { type: "text_delta", text } });
    return events;
  }

  private flushStripper(): StreamEvent[] {
    const rest = this.stripper?.flush();
    return rest ? this.text(rest) : [];
  }

  private close(): StreamEvent[] {
    if (!this.open) return [];
    const index = this.open.index;
    this.open = undefined;
    return [{ type: "content_block_stop", index }];
  }
}

export function sse(event: StreamEvent): string {
  return `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
}
