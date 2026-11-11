"""Fake copilot-api (Anthropic side) that answers Claude Code's main turn with a
pre-translated event stream, then answers every later turn with plain "done.".

The "main turn" is a streaming messages request with tools whose last message has
no tool_result block: that is the request in which the model is expected to call
tools. Its reply is the event list for SCENARIO from EVENTS (events.json, produced
by copilot-api's own translateChunkToAnthropicEvents), written as SSE the way
hono's streamSSE does (event + data lines), then the connection is ended.
No credentials, no network. Logs every request as JSONL.
Usage: SCENARIO=name EVENTS=events.json python3 fake_upstream.py <port> <log.jsonl>
"""
import itertools
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, LOG = int(sys.argv[1]), sys.argv[2]
EVENTS = json.load(open(os.environ["EVENTS"]))[os.environ["SCENARIO"]]
COUNT_SUFFIX = "count" + "_tokens"
ids = itertools.count(1)


def sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def done_events(text="done."):
    n = next(ids)
    return [
        {"type": "message_start", "message": {"id": f"msg_d{n}", "type": "message", "role": "assistant",
         "model": "fake", "content": [], "stop_reason": None, "stop_sequence": None,
         "usage": {"input_tokens": 10, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"input_tokens": 10, "output_tokens": 2}},
        {"type": "message_stop"},
    ]


def summarize(body):
    msgs = body.get("messages") or []
    last = msgs[-1] if msgs else {}
    content = last.get("content")
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
    results = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            c = b.get("content")
            if isinstance(c, list):
                c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
            results.append({"id": b.get("tool_use_id"), "is_error": b.get("is_error", False),
                            "content": str(c)[:160]})
    prev = msgs[-2] if len(msgs) > 1 else {}
    prev_blocks = []
    if prev.get("role") == "assistant" and isinstance(prev.get("content"), list):
        prev_blocks = [{"type": b.get("type"), "id": b.get("id"), "input": b.get("input"),
                        "text": (b.get("text") or "")[:80]}
                       for b in prev["content"] if isinstance(b, dict)]
    texts = [b.get("text", "")[:200] for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    return {"model": body.get("model"), "stream": bool(body.get("stream")), "n_tools": len(body.get("tools") or []),
            "n_messages": len(msgs), "tool_results": results, "assistant_replayed": prev_blocks,
            "last_texts": texts}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, data, close=False):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        if close:
            self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(data)
        if close:
            self.close_connection = True

    def do_GET(self):
        self._send(200, "application/json", b'{"data":[]}')

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        path = self.path.split("?")[0]
        if path.endswith(COUNT_SUFFIX):
            self._send(200, "application/json", b'{"input_tokens": 100}')
            return
        rec = {"path": path, "x_retry": self.headers.get("x-stainless-retry-count")}
        rec.update(summarize(body))
        main = rec["stream"] and rec["n_tools"] > 3 and not rec["tool_results"]
        rec["served"] = "scenario" if main else "done"
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
        if not rec["stream"]:
            out = {"id": f"msg_{next(ids)}", "type": "message", "role": "assistant", "model": "fake",
                   "content": [{"type": "text", "text": "done."}], "stop_reason": "end_turn",
                   "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 2}}
            self._send(200, "application/json", json.dumps(out).encode())
            return
        # Like hono streamSSE: the response simply ends after the last event.
        self._send(200, "text/event-stream", sse(EVENTS if main else done_events()), close=True)


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
