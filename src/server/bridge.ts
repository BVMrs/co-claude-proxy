// The bridge: one object holding upstream state, and the /v1/messages pipeline.

import fs from "node:fs";
import path from "node:path";

import type { Config } from "../config.ts";
import type { CopilotClient } from "../copilot/client.ts";
import { readSse } from "../copilot/sse.ts";
import type { ModelRegistry } from "../copilot/models.ts";
import { color, log } from "../log.ts";
import { prefillCachePath } from "../paths.ts";
import { estimateTokens } from "../pipeline/count-tokens.ts";
import { errorBody, isModelNotSupported, isPrefillError, upstreamMessage } from "../pipeline/errors.ts";
import { fromOpenAI } from "../pipeline/from-openai.ts";
import { rejectsPrefill, repairRequest, type RepairResult } from "../pipeline/repair.ts";
import { pickSlots, resolveModel, type Resolution, type Slots } from "../pipeline/resolve-model.ts";
import { sse, StreamTranslator } from "../pipeline/stream.ts";
import { toOpenAI } from "../pipeline/to-openai.ts";
import type { ErrorBody, MessagesRequest, StreamEvent, Usage } from "../types/anthropic.ts";
import type { ChatChunk, ChatResponse } from "../types/openai.ts";
import { BodyLog } from "./body-log.ts";

export interface Reply {
  status: number;
  json?: unknown;
  /** Streaming reply: called once headers are decided. */
  stream?: (write: (chunk: string) => void, signal: AbortSignal) => Promise<void>;
  headers?: Record<string, string>;
}

export class HttpError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const PING_MS = 15_000;

export class Bridge {
  readonly cfg: Config;
  readonly client: CopilotClient;
  readonly registry: ModelRegistry;
  slots: Slots = {};
  /** model -> rejects prefill (true) / accepts (false), learned from 400s or `doctor --probe`. */
  readonly prefillVerdicts = new Map<string, boolean>();
  private seq = 0;
  private lastUpstreamAt = 0;
  private readonly bodies: BodyLog;
  readonly startedAt = Date.now();
  /** Keep-alive ping interval while streaming (tests shorten it). */
  pingMs = PING_MS;

  constructor(cfg: Config, client: CopilotClient, registry: ModelRegistry) {
    this.cfg = cfg;
    this.client = client;
    this.registry = registry;
    this.bodies = new BodyLog(cfg.logBodies);
    this.loadPrefillVerdicts();
  }

  /** Load models and choose the model slots. Throws if the slots asked for don't exist. */
  async init(): Promise<void> {
    const ids = (await this.registry.list()).map((m) => m.id);
    for (const [flag, id] of [["--model", this.cfg.model], ["--small-model", this.cfg.smallModel]] as const) {
      if (id && !ids.includes(id)) {
        throw new Error(`${flag} ${id} isn't available on this Copilot account. Use one of: ${ids.join(", ")}`);
      }
    }
    this.slots = pickSlots(ids, { main: this.cfg.model, small: this.cfg.smallModel });
  }

  // ---- prefill verdict cache ------------------------------------------------

  private loadPrefillVerdicts(): void {
    try {
      const data = JSON.parse(fs.readFileSync(prefillCachePath(), "utf8")) as Record<string, boolean>;
      for (const [k, v] of Object.entries(data)) if (typeof v === "boolean") this.prefillVerdicts.set(k, v);
    } catch {
      // no cache yet
    }
  }

  recordPrefillVerdict(model: string, rejects: boolean): void {
    if (this.prefillVerdicts.get(model) === rejects) return;
    this.prefillVerdicts.set(model, rejects);
    const file = prefillCachePath();
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, JSON.stringify(Object.fromEntries(this.prefillVerdicts), null, 2) + "\n");
    } catch (e) {
      log.debug(`could not save prefill verdicts: ${(e as Error).message}`);
    }
  }

  // ---- shared helpers --------------------------------------------------------

  async resolve(requested: string): Promise<Resolution> {
    let ids: string[];
    try {
      ids = (await this.registry.list()).map((m) => m.id);
    } catch (e) {
      throw new HttpError(502, `Couldn't load the Copilot model list: ${(e as Error).message}`);
    }
    return resolveModel(requested, ids, {
      aliases: this.cfg.modelAliases,
      mainSlot: this.slots.main,
      opusSlot: this.slots.opus,
      sonnetSlot: this.slots.sonnet,
      smallSlot: this.slots.small,
      strict: this.cfg.strictModels,
    });
  }

  private async rateLimit(): Promise<void> {
    const gap = this.cfg.rateLimit;
    if (!gap) return;
    const wait = this.lastUpstreamAt + gap * 1000 - Date.now();
    if (wait > 0) {
      if (!this.cfg.wait) throw new HttpError(429, `Rate limit: wait ${Math.ceil(wait / 1000)}s between requests (--rate-limit).`);
      log.verbose(`rate limit: waiting ${Math.ceil(wait / 1000)}s`);
      await new Promise((r) => setTimeout(r, wait));
    }
    this.lastUpstreamAt = Date.now();
  }

  nextSeq(): number {
    return ++this.seq;
  }

  // ---- POST /v1/messages ----------------------------------------------------

  async messages(raw: unknown, signal: AbortSignal): Promise<Reply> {
    const seq = this.nextSeq();
    const t0 = Date.now();
    const req = validateMessagesRequest(raw);
    this.bodies.write(seq, "request.json", req);

    let res = await this.resolve(req.model);
    const summary = new Summary(seq, req, this.cfg.verbose);
    if (!res.ok) {
      summary.finish(404, t0);
      return { status: 404, json: errorBody(404, res.message) };
    }
    summary.resolved(res.id, res.how);
    if (res.how === "slot") log.info(`model ${req.model} isn't on this account; using ${res.id} (slot fallback, --strict-models turns this off)`);

    let reloadedModels = false;
    let learnedPrefill = false;
    for (;;) {
      const model = res.id;
      const repair = repairRequest(req, {
        mode: this.cfg.prefill,
        modelRejectsPrefill: rejectsPrefill(model, this.prefillVerdicts),
      });
      summary.repaired(repair);
      if (repair.prefill !== "none" && repair.prefill !== "kept") {
        log.verbose(`#${seq} prefill ${repair.prefill} for ${model}`);
      }
      const tr = toOpenAI(repair.request, model);
      if (tr.droppedTools.length) log.debug(`#${seq} dropped non-function tools: ${tr.droppedTools.join(", ")}`);
      this.bodies.write(seq, "upstream-request.json", tr.payload);

      await this.rateLimit();
      let upstream: Response;
      try {
        upstream = await this.client.chat(tr.payload, { vision: tr.vision, initiator: tr.initiator, signal });
      } catch (e) {
        if (signal.aborted) throw e;
        summary.finish(502, t0);
        return { status: 502, json: errorBody(502, `Couldn't reach the Copilot API: ${(e as Error).message}`) };
      }

      if (!upstream.ok) {
        const text = await upstream.text();
        this.bodies.write(seq, "upstream-error.txt", text);
        if (upstream.status === 400 && isPrefillError(text) && repair.prefill === "kept" && !learnedPrefill && this.cfg.prefill !== "off") {
          learnedPrefill = true;
          this.recordPrefillVerdict(model, true);
          log.info(`${model} rejects assistant prefill; remembering that and retrying with the repair`);
          continue;
        }
        if (isModelNotSupported(text) && !reloadedModels) {
          reloadedModels = true;
          log.info(`Copilot says ${model} isn't supported; reloading the model list`);
          await this.registry.reload().catch(() => {});
          const again = await this.resolve(req.model);
          if (again.ok && again.id !== model) {
            res = again;
            summary.resolved(again.id, again.how);
            continue;
          }
        }
        const { message, code } = upstreamMessage(text);
        let hint = "";
        if (isPrefillError(text)) hint = " (co-claude-bridge: the request still ended on an assistant turn; try --prefill convert)";
        else if (isModelNotSupported(text)) hint = ` (co-claude-bridge: use a model from \`co-claude-bridge models\`)`;
        summary.finish(upstream.status, t0, code ?? message.slice(0, 80));
        log.warn(`#${seq} Copilot ${upstream.status} for ${model}: ${message.slice(0, 300)}`);
        return { status: upstream.status, json: errorBody(upstream.status, `${message}${hint}`) };
      }

      if (repair.prefill === "kept" && repair.lastRole === "assistant" && !this.prefillVerdicts.has(model)) {
        this.recordPrefillVerdict(model, false);
      }

      if (!req.stream) {
        const json = (await upstream.json()) as ChatResponse;
        this.bodies.write(seq, "upstream-response.json", json);
        const out = fromOpenAI(json, req.model, repair.prefillText);
        summary.finish(200, t0, undefined, out.usage);
        return { status: 200, json: out };
      }

      return {
        status: 200,
        headers: { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "keep-alive" },
        stream: (write, sig) => this.pipeStream(seq, upstream, req.model, repair, write, sig, summary, t0),
      };
    }
  }

  private async pipeStream(
    seq: number,
    upstream: Response,
    model: string,
    repair: RepairResult,
    write: (chunk: string) => void,
    signal: AbortSignal,
    summary: Summary,
    t0: number,
  ): Promise<void> {
    const tr = new StreamTranslator(model, repair.prefillText);
    const emit = (events: StreamEvent[]) => {
      for (const e of events) write(sse(e));
    };
    const raw: string[] = [];
    const ping = setInterval(() => write(sse({ type: "ping" })), this.pingMs);
    let failed: string | undefined;
    try {
      emit(tr.start());
      for await (const ev of readSse(upstream.body!)) {
        if (signal.aborted) break;
        if (this.bodies.enabled) raw.push(ev.data);
        if (ev.data === "[DONE]") break;
        if (!ev.data) continue;
        let chunk: ChatChunk & { error?: { message?: string } };
        try {
          chunk = JSON.parse(ev.data);
        } catch {
          log.debug(`#${seq} skipping unparseable stream data: ${ev.data.slice(0, 120)}`);
          continue;
        }
        if (chunk.error) {
          failed = chunk.error.message ?? "upstream error";
          break;
        }
        emit(tr.chunk(chunk));
      }
    } catch (e) {
      if (!signal.aborted) failed = (e as Error).message;
    } finally {
      clearInterval(ping);
    }
    if (signal.aborted) {
      summary.finish(499, t0, "client closed");
      return;
    }
    if (failed) {
      log.warn(`#${seq} stream failed: ${failed}`);
      write(sse({ type: "error", error: { type: "api_error", message: `Copilot stream failed: ${failed}` } }));
      summary.finish(502, t0, "stream error");
    } else {
      emit(tr.end());
      summary.finish(200, t0, undefined, tr.usageSeen());
    }
    if (this.bodies.enabled) this.bodies.write(seq, "upstream-stream.jsonl", raw.join("\n") + "\n");
  }

  // ---- other routes -----------------------------------------------------------

  async countTokens(raw: unknown): Promise<Reply> {
    const req = validateMessagesRequest(raw);
    const repaired = repairRequest(req, { mode: "off", modelRejectsPrefill: false });
    return { status: 200, json: { input_tokens: estimateTokens(repaired.request) } };
  }

  async listModels(): Promise<Reply> {
    const models = await this.registry.list();
    const data = models.map((m) => ({
      type: "model",
      id: m.id,
      display_name: m.name ?? m.id,
      created_at: "1970-01-01T00:00:00Z",
    }));
    return { status: 200, json: { data, has_more: false, first_id: data[0]?.id ?? null, last_id: data.at(-1)?.id ?? null } };
  }

  /** POST /v1/chat/completions: OpenAI-format passthrough with model resolution. */
  async chatPassthrough(raw: unknown, signal: AbortSignal): Promise<Reply> {
    if (!raw || typeof raw !== "object") throw new HttpError(400, "Request body must be a JSON object.");
    const body = { ...(raw as Record<string, unknown>) };
    if (typeof body.model !== "string") throw new HttpError(400, "`model` is required.");
    const res = await this.resolve(body.model);
    if (!res.ok) return { status: 404, json: { error: { message: res.message, type: "invalid_request_error", code: "model_not_found" } } };
    body.model = res.id;
    await this.rateLimit();
    const upstream = await this.client.chat(body as never, { signal });
    log.verbose(`#${this.nextSeq()} POST /v1/chat/completions ${res.id} → ${upstream.status}`);
    const type = upstream.headers.get("content-type") ?? "application/json";
    return {
      status: upstream.status,
      headers: { "content-type": type },
      stream: async (write) => {
        if (!upstream.body) return;
        const decoder = new TextDecoder();
        for await (const part of upstream.body as unknown as AsyncIterable<Uint8Array>) write(decoder.decode(part, { stream: true }));
      },
    };
  }

  health(): Reply {
    return {
      status: 200,
      json: {
        status: "ok",
        uptime_s: Math.round((Date.now() - this.startedAt) / 1000),
        models: this.registry.cached().length,
        slots: this.slots,
        prefill_mode: this.cfg.prefill,
      },
    };
  }
}

export function validateMessagesRequest(raw: unknown): MessagesRequest {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new HttpError(400, "Request body must be a JSON object.");
  const r = raw as MessagesRequest;
  if (typeof r.model !== "string" || !r.model) throw new HttpError(400, "`model` is required.");
  if (!Array.isArray(r.messages)) throw new HttpError(400, "`messages` must be an array.");
  for (const m of r.messages) {
    if (!m || typeof m !== "object" || !["user", "assistant", "system"].includes(m.role)) {
      throw new HttpError(400, "Each message needs a role of user, assistant or system.");
    }
  }
  return r;
}

/** One log line per request; printed with --verbose, and always for failures. */
class Summary {
  private resolvedTo?: string;
  private how?: string;
  private repair?: RepairResult;
  private readonly seq: number;
  private readonly req: MessagesRequest;
  private readonly verbose: number;

  constructor(seq: number, req: MessagesRequest, verbose: number) {
    this.seq = seq;
    this.req = req;
    this.verbose = verbose;
  }

  resolved(id: string, how: string) {
    this.resolvedTo = id;
    this.how = how;
  }

  repaired(r: RepairResult) {
    this.repair = r;
  }

  finish(status: number, t0: number, note?: string, usage?: Usage) {
    const ok = status >= 200 && status < 300;
    if (ok && this.verbose < 1) return;
    const parts = [`#${this.seq} POST /v1/messages`, this.req.model];
    if (this.resolvedTo && this.resolvedTo !== this.req.model) parts.push(`→ ${this.resolvedTo} (${this.how})`);
    parts.push(`msgs=${this.req.messages.length}`, `last=${this.req.messages.at(-1)?.role ?? "-"}`);
    parts.push(`tools=${this.req.tools?.length ?? 0}`);
    if (this.req.stream) parts.push("stream");
    if (this.repair?.systemFolded) parts.push(`system_folded=${this.repair.systemFolded}`);
    if (this.repair && this.repair.prefill !== "none") parts.push(`prefill=${this.repair.prefill}`);
    const s = ok ? color.green(String(status)) : color.red(String(status));
    parts.push(`→ ${s}`, `${((Date.now() - t0) / 1000).toFixed(1)}s`);
    if (usage) parts.push(`in=${usage.input_tokens + (usage.cache_read_input_tokens ?? 0)} out=${usage.output_tokens}`);
    if (note) parts.push(color.dim(note));
    (ok ? log.info : log.warn)(parts.join(" "));
  }
}

export type { ErrorBody };
