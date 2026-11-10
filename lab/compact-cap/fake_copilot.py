"""Fake copilot-api (Anthropic side) that enforces a Copilot-style prompt-token cap.

No model, no credentials, no network. Each /v1/messages request is given a *virtual*
prompt size so that a short real session can reach 100k+ tokens:

    virtual = len(body_bytes) // 4 + PAD * (number of "<<PAD>>" markers in messages)

Env knobs:
  CAP      virtual prompt cap (0 = none). Above it the request is rejected.
  ERRFMT   copilot   -> what copilot-api 0.7.0 forwards (src/lib/error.ts forwardError):
                        400 {"error":{"message":"<Copilot JSON as text>","type":"error"}}
           anthropic -> 400 invalid_request_error "prompt is too long: N tokens > CAP maximum"
  USAGE    delta  -> copilot-api stream translation when Copilot's first chunk has no usage:
                     message_start input_tokens=0, message_delta input_tokens=N
           start  -> Anthropic-like: message_start input_tokens=N
           none   -> usage lost (Copilot sends usage only in a choices:[] chunk, which
                     copilot-api 0.7.0 drops): input_tokens=0 in both events
  PAD      virtual tokens per marker (default 10000)
A request whose text contains "detailed summary of the conversation" is treated as a
compaction call and answered with a short <summary>.
Usage: python3 fake_copilot.py <port> <log.jsonl>
"""
import itertools
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, LOG = int(sys.argv[1]), sys.argv[2]
CAP = int(os.environ.get("CAP", "0"))
ERRFMT = os.environ.get("ERRFMT", "copilot")
USAGE = os.environ.get("USAGE", "delta")
PAD = int(os.environ.get("PAD", "10000"))
ids = itertools.count(1)


def sse(text, n_in):
    n = next(ids)
    start_in = n_in if USAGE == "start" else 0
    if USAGE == "none":
        n_in = 0
    ev = [
        ("message_start", {"type": "message_start", "message": {
            "id": f"msg_{n}", "type": "message", "role": "assistant", "model": "fake", "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": start_in, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"input_tokens": n_in, "output_tokens": 20}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in ev).encode()


def error_body(n_in):
    if ERRFMT == "anthropic":
        return json.dumps({"type": "error", "error": {
            "type": "invalid_request_error",
            "message": f"prompt is too long: {n_in} tokens > {CAP} maximum"}}).encode()
    copilot = json.dumps({"error": {
        "message": f"prompt token count of {n_in} exceeds the limit of {CAP}",
        "code": "model_max_prompt_tokens_exceeded"}})
    return json.dumps({"error": {"message": copilot, "type": "error"}}).encode()


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
        except ValueError:
            body = {}
        msgs = json.dumps(body.get("messages", []))
        n_in = len(raw) // 4 + PAD * msgs.count("<<PAD>>")
        path = self.path.split("?")[0]
        compact = "detailed summary of the conversation" in raw.decode("utf-8", "replace")
        rec = {"path": path, "model": body.get("model"), "virtual": n_in, "bytes": len(raw),
               "n_messages": len(body.get("messages", [])), "compact": compact,
               "max_tokens": body.get("max_tokens")}
        if path.endswith("/count_tokens"):
            rec["status"] = 200
            self._log(rec)
            return self._send(200, "application/json", json.dumps({"input_tokens": n_in}).encode())
        if CAP and n_in > CAP:
            rec["status"] = 400
            self._log(rec)
            return self._send(400, "application/json", error_body(n_in))
        rec["status"] = 200
        self._log(rec)
        text = "<summary>Earlier turns: the user sent numbered filler turns.</summary>" if compact else "ok."
        if body.get("stream"):
            return self._send(200, "text/event-stream", sse(text, n_in))
        out = {"id": f"msg_{next(ids)}", "type": "message", "role": "assistant", "model": "fake",
               "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
               "usage": {"input_tokens": n_in, "output_tokens": 20}}
        self._send(200, "application/json", json.dumps(out).encode())

    def _log(self, rec):
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
