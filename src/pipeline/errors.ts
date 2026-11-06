import type { ErrorBody, ErrorType } from "../types/anthropic.ts";

export function errorType(status: number): ErrorType {
  if (status === 400 || status === 422) return "invalid_request_error";
  if (status === 401) return "authentication_error";
  if (status === 403) return "permission_error";
  if (status === 404) return "not_found_error";
  if (status === 413) return "request_too_large";
  if (status === 429) return "rate_limit_error";
  if (status === 503 || status === 529) return "overloaded_error";
  return "api_error";
}

export function errorBody(status: number, message: string): ErrorBody {
  return { type: "error", error: { type: errorType(status), message } };
}

/** Pull a readable message out of an upstream error body. */
export function upstreamMessage(body: string): { message: string; code?: string } {
  try {
    const j = JSON.parse(body) as { error?: { message?: string; code?: string; type?: string } | string; message?: string };
    if (typeof j.error === "string") return { message: j.error };
    const message = j.error?.message ?? j.message ?? body;
    return { message, code: j.error?.code ?? j.error?.type };
  } catch {
    return { message: body.trim() || "(empty response body)" };
  }
}

export const isPrefillError = (body: string) => /assistant message prefill|must end with a user message|last message .* must not be from the assistant/i.test(body);
export const isModelNotSupported = (body: string) => /model_not_supported|requested model is not supported/i.test(body);
