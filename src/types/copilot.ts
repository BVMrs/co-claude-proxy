export interface CopilotModel {
  id: string;
  name?: string;
  vendor?: string;
  version?: string;
  preview?: boolean;
  model_picker_enabled?: boolean;
  capabilities?: {
    family?: string;
    type?: string;
    limits?: {
      max_context_window_tokens?: number;
      max_output_tokens?: number;
      max_prompt_tokens?: number;
    };
    supports?: { tool_calls?: boolean; parallel_tool_calls?: boolean; vision?: boolean; streaming?: boolean };
  };
  supported_endpoints?: string[];
  policy?: { state?: string; terms?: string };
}

export interface CopilotModelsResponse {
  data: CopilotModel[];
}

export interface CopilotTokenResponse {
  token: string;
  expires_at: number;
  refresh_in?: number;
  endpoints?: { api?: string };
}
