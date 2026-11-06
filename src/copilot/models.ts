import { log } from "../log.ts";
import type { CopilotModel } from "../types/copilot.ts";
import type { CopilotClient } from "./client.ts";

const TTL_MS = 30 * 60 * 1000;

/** Cached view of the account's /models list. */
export class ModelRegistry {
  private models: CopilotModel[] = [];
  private loadedAt = 0;
  private inflight?: Promise<CopilotModel[]>;
  private readonly client: Pick<CopilotClient, "models">;

  constructor(client: Pick<CopilotClient, "models">) {
    this.client = client;
  }

  async list(): Promise<CopilotModel[]> {
    if (this.models.length && Date.now() - this.loadedAt < TTL_MS) return this.models;
    return this.reload();
  }

  reload(): Promise<CopilotModel[]> {
    if (!this.inflight) {
      this.inflight = this.client
        .models()
        .then((r) => {
          // Keep chat models only; embeddings and the like can't serve /v1/messages.
          const all = r.data ?? [];
          const chat = all.filter((m) => !m.capabilities?.type || m.capabilities.type === "chat");
          this.models = dedupe(chat);
          this.loadedAt = Date.now();
          log.debug(`loaded ${this.models.length} chat models`);
          return this.models;
        })
        .finally(() => {
          this.inflight = undefined;
        });
    }
    return this.inflight;
  }

  /** Last loaded list without a network call (may be empty). */
  cached(): CopilotModel[] {
    return this.models;
  }

  ids(): string[] {
    return this.models.map((m) => m.id);
  }

  get(id: string): CopilotModel | undefined {
    return this.models.find((m) => m.id === id);
  }
}

function dedupe(models: CopilotModel[]): CopilotModel[] {
  const seen = new Set<string>();
  return models.filter((m) => (seen.has(m.id) ? false : (seen.add(m.id), true)));
}
