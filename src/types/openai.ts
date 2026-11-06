// Subset of the OpenAI Chat Completions API that api.githubcopilot.com speaks.

export interface TextPart {
  type: "text";
  text: string;
}

export interface ImagePart {
  type: "image_url";
  image_url: { url: string; detail?: "low" | "high" | "auto" };
}

export type ContentPart = TextPart | ImagePart;

export interface ToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export type ChatMessage =
  | { role: "system"; content: string }
  | { role: "user"; content: string | ContentPart[] }
  | { role: "assistant"; content: string | null; tool_calls?: ToolCall[] }
  | { role: "tool"; tool_call_id: string; content: string };

export interface ChatTool {
  type: "function";
  function: { name: string; description?: string; parameters: Record<string, unknown> };
}

export type ChatToolChoice = "auto" | "none" | "required" | { type: "function"; function: { name: string } };

export interface ChatRequest {
  model: string;
  messages: ChatMessage[];
  max_tokens?: number;
  temperature?: number;
  top_p?: number;
  stop?: string[];
  stream?: boolean;
  stream_options?: { include_usage: boolean };
  tools?: ChatTool[];
  tool_choice?: ChatToolChoice;
  parallel_tool_calls?: boolean;
}

export type FinishReason = "stop" | "length" | "tool_calls" | "content_filter" | "function_call" | null;

export interface ChatUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  prompt_tokens_details?: { cached_tokens?: number };
}

export interface ChatResponse {
  id: string;
  model?: string;
  choices: Array<{
    index: number;
    message: { role: "assistant"; content: string | null; tool_calls?: ToolCall[] };
    finish_reason: FinishReason;
  }>;
  usage?: ChatUsage;
}

export interface ChatChunk {
  id?: string;
  model?: string;
  choices?: Array<{
    index?: number;
    delta?: {
      role?: string;
      content?: string | null;
      tool_calls?: Array<{
        index?: number;
        id?: string;
        type?: "function";
        function?: { name?: string; arguments?: string };
      }>;
    };
    finish_reason?: FinishReason;
  }>;
  usage?: ChatUsage | null;
}
