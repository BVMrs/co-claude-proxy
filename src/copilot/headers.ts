import { randomUUID } from "node:crypto";

import { checkConfiguredUrl } from "../security/upstream.ts";

// The Copilot API only serves clients that identify as a Copilot editor
// integration. These match what VS Code Copilot Chat sends (and what
// ericc-ch/copilot-api uses). Both can be overridden if GitHub starts
// rejecting an old version.
export const COPILOT_CHAT_VERSION = process.env.COB_COPILOT_CHAT_VERSION ?? "0.26.7";
export const VSCODE_VERSION = process.env.COB_VSCODE_VERSION ?? "1.104.3";
const API_VERSION = "2025-04-01";

export const GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98";
export const GITHUB_SCOPES = "read:user";

/** github.com, or COB_GITHUB_BASE_URL (https, or http to localhost for tests). Read per call. */
export function githubBaseUrl(env: NodeJS.ProcessEnv = process.env): string {
  return env.COB_GITHUB_BASE_URL ? checkConfiguredUrl(env.COB_GITHUB_BASE_URL, "COB_GITHUB_BASE_URL").replace(/\/+$/, "") : "https://github.com";
}

/** api.github.com, or COB_GITHUB_API_URL (https, or http to localhost for tests). Read per call. */
export function githubApiUrl(env: NodeJS.ProcessEnv = process.env): string {
  return env.COB_GITHUB_API_URL ? checkConfiguredUrl(env.COB_GITHUB_API_URL, "COB_GITHUB_API_URL").replace(/\/+$/, "") : "https://api.github.com";
}

const editorHeaders = () => ({
  "editor-version": `vscode/${VSCODE_VERSION}`,
  "editor-plugin-version": `copilot-chat/${COPILOT_CHAT_VERSION}`,
  "user-agent": `GitHubCopilotChat/${COPILOT_CHAT_VERSION}`,
  "x-github-api-version": API_VERSION,
});

export function githubHeaders(githubToken: string): Record<string, string> {
  return {
    accept: "application/json",
    "content-type": "application/json",
    authorization: `token ${githubToken}`,
    ...editorHeaders(),
  };
}

export type Initiator = "user" | "agent";

export function copilotHeaders(
  copilotToken: string,
  opts: { vision?: boolean; initiator?: Initiator } = {},
): Record<string, string> {
  const h: Record<string, string> = {
    authorization: `Bearer ${copilotToken}`,
    "content-type": "application/json",
    accept: "application/json",
    "copilot-integration-id": "vscode-chat",
    "openai-intent": "conversation-panel",
    "x-request-id": randomUUID(),
    "x-vscode-user-agent-library-version": "electron-fetch",
    ...editorHeaders(),
  };
  if (opts.vision) h["copilot-vision-request"] = "true";
  if (opts.initiator) h["x-initiator"] = opts.initiator;
  return h;
}

export function defaultCopilotBaseUrl(accountType: string): string {
  return accountType === "individual"
    ? "https://api.githubcopilot.com"
    : `https://api.${accountType}.githubcopilot.com`;
}
