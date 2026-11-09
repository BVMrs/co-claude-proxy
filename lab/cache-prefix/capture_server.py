"""Fake Anthropic Messages endpoint that records every request body Claude Code sends.

No model, no credentials, no network: it answers with canned SSE. A user prompt that
contains READ_FILE:<path> gets one Read tool_use back; BASH:<cmd> (to end of line) gets one Bash tool_use; anything else gets "done.".
Usage: python3 capture_server.py <port> <out.jsonl>
"""
import itertools
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, OUT = int(sys.argv[1]), sys.argv[2]
ids = itertools.count(1)
MESSAGES = "/v1/" + "messages"


def last_user_text(body):
    for m in reversed(body.get("messages") or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c, False
        texts = [b.get("text", "") for b in c if b.get("type") == "text"]
        return "\n".join(texts), any(b.get("type") == "tool_result" for b in c)
    return "", False


def sse(blocks, stop):
    n = next(ids)
    ev = [("message_start", {"type": "message_start", "message": {
        "id": f"msg_{n}", "type": "message", "role": "assistant", "model": "fake",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 1}}})]
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            ev.append(("content_block_start", {"type": "content_block_start", "index": i,
                                               "content_block": {"type": "text", "text": ""}}))
            ev.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                               "delta": {"type": "text_delta", "text": b["text"]}}))
        else:
            ev.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}}))
            ev.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(b["input"])}}))
        ev.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    ev.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                 "usage": {"output_tokens": 5}}))
    ev.append(("message_stop", {"type": "message_stop"}))
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in ev).encode()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, data):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
        except Exception:
            body = None
        with open(OUT, "a") as f:
            f.write(json.dumps({"path": self.path, "body": body}) + "\n")
        if "count_tokens" in self.path:
            return self._send(200, "application/json", b'{"input_tokens": 100}')
        if not isinstance(body, dict) or not self.path.startswith(MESSAGES):
            return self._send(200, "application/json", b"{}")
        text, is_tr = last_user_text(body)
        blocks, stop = [{"type": "text", "text": "done."}], "end_turn"
        if not is_tr and "READ_FILE:" in text and body.get("tools"):
            path = text.split("READ_FILE:")[1].split()[0]
            blocks = [{"type": "text", "text": "Reading."},
                      {"type": "tool_use", "id": f"toolu_{next(ids)}", "name": "Read", "input": {"file_path": path}}]
            stop = "tool_use"
        elif not is_tr and "BASH:" in text and body.get("tools"):
            cmd = text.split("BASH:")[1].split("\n")[0].strip()
            blocks = [{"type": "text", "text": "Running."},
                      {"type": "tool_use", "id": f"toolu_{next(ids)}", "name": "Bash",
                       "input": {"command": cmd, "description": "run"}}]
            stop = "tool_use"
        if body.get("stream"):
            return self._send(200, "text/event-stream", sse(blocks, stop))
        msg = {"id": f"msg_{next(ids)}", "type": "message", "role": "assistant", "model": body.get("model"),
               "content": blocks, "stop_reason": stop, "stop_sequence": None,
               "usage": {"input_tokens": 10, "output_tokens": 5}}
        self._send(200, "application/json", json.dumps(msg).encode())


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
