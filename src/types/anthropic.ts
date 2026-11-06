// Subset of the Anthropic Messages API that Claude Code sends and expects back.

export interface CacheControl {
  type: "ephemeral";
  ttl?: string;
}

export interface TextBlock {
  type: "text";
  text: string;
  cache_control?: CacheControl;
}

export interface ImageBlock {
  type: "image";
  source:
    | { type: "base64"; media_type: string; data: string }
    | { type: "url"; url: string };
  cache_control?: CacheControl;
}

export interface DocumentBlock {
  type: "document";
  source: unknown;
  title?: string;
}

export interface ToolUseBlock {
  type: "tool_use";
  id: string;
  name: string;
  input: unknown;
  cache_control?: CacheControl;
}

export interface ToolResultBlock {
  type: "tool_result";
  tool_use_id: string;
  content?: string | Array<TextBlock | ImageBlock | DocumentBlock>;
  is_error?: boolean;
  cache_control?: CacheControl;
}

export interface ThinkingBlock {
  type: "thinking";
  thinking: string;
  signature?: string;
}

export interface RedactedThinkingBlock {
  type: "redacted_thinking";
  data: string;
}

export interface UnknownBlock {
  type: string;
  [key: string]: unknown;
}

export type ContentBlock =
  | TextBlock
  | ImageBlock
  | DocumentBlock
  | ToolUseBlock
  | ToolResultBlock
  | ThinkingBlock
  | RedactedThinkingBlock;

/** Claude Code also sends role "system" inside messages (2.1.x environment block). */
export type Role = "user" | "assistant" | "system";

export interface Message {
  role: Role;
  content: string | ContentBlock[];
}

export interface Tool {
  name: string;
  description?: string;
  input_schema?: Record<string, unknown>;
  type?: string;
  cache_control?: CacheControl;
}

export type ToolChoice =
  | { type: "auto"; disable_parallel_tool_use?: boolean }
  | { type: "any"; disable_parallel_tool_use?: boolean }
  | { type: "tool"; name: string; disable_parallel_tool_use?: boolean }
  | { type: "none" };

export interface MessagesRequest {
  model: string;
  messages: Message[];
  max_tokens: number;
  system?: string | TextBlock[];
  stop_sequences?: string[];
  stream?: boolean;
  temperature?: number;
  top_p?: number;
  top_k?: number;
  tools?: Tool[];
  tool_choice?: ToolChoice;
  metadata?: { user_id?: string };
  thinking?: unknown;
  [key: string]: unknown;
}

export type StopReason = "end_turn" | "max_tokens" | "stop_sequence" | "tool_use" | "pause_turn" | "refusal";

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
}

export interface MessagesResponse {
  id: string;
  type: "message";
  role: "assistant";
  model: string;
  content: Array<TextBlock | ToolUseBlock>;
  stop_reason: StopReason | null;
  stop_sequence: string | null;
  usage: Usage;
}

export type StreamEvent =
  | { type: "message_start"; message: MessagesResponse }
  | { type: "content_block_start"; index: number; content_block: TextBlock | ToolUseBlock }
  | {
      type: "content_block_delta";
      index: number;
      delta: { type: "text_delta"; text: string } | { type: "input_json_delta"; partial_json: string };
    }
  | { type: "content_block_stop"; index: number }
  | { type: "message_delta"; delta: { stop_reason: StopReason | null; stop_sequence: string | null }; usage: Usage }
  | { type: "message_stop" }
  | { type: "ping" }
  | { type: "error"; error: { type: string; message: string } };

export type ErrorType =
  | "invalid_request_error"
  | "authentication_error"
  | "permission_error"
  | "not_found_error"
  | "request_too_large"
  | "rate_limit_error"
  | "api_error"
  | "overloaded_error";

export interface ErrorBody {
  type: "error";
  error: { type: ErrorType; message: string };
}
