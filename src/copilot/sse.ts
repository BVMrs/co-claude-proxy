/** Parse a text/event-stream body into its events. */
export interface SseEvent {
  event?: string;
  data: string;
}

export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const decoder = new TextDecoder();
  let buf = "";
  const reader = body.getReader();
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = findBoundary(buf)) >= 0) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx).replace(/^(\r?\n){2}/, "");
        const ev = parseEvent(raw);
        if (ev) yield ev;
      }
    }
    buf += decoder.decode();
    const ev = parseEvent(buf);
    if (ev) yield ev;
  } finally {
    reader.releaseLock();
  }
}

function findBoundary(s: string): number {
  const a = s.indexOf("\n\n");
  const b = s.indexOf("\r\n\r\n");
  if (a < 0) return b;
  if (b < 0) return a;
  return Math.min(a, b);
}

function parseEvent(raw: string): SseEvent | undefined {
  let event: string | undefined;
  const data: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (!data.length && !event) return undefined;
  return { event, data: data.join("\n") };
}
