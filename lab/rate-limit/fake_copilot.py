"""Fake copilot-api (Anthropic side) that answers some messages requests with an error.

No model, no credentials, no network. Logs every request (time, model, status) as JSONL.

Env knobs:
  FAIL      count  -> the first K messages requests fail, then all succeed
            window -> every request in the first WINDOW seconds (from the first request) fails
            always -> every request fails
            none   -> nothing fails
  K, WINDOW         see above
  STATUS    HTTP status of the failure (default 429)
  FMT       copilot   -> copilot-api 0.7.0 forwardError: {"error":{"message":"<Copilot JSON as text>","type":"error"}}
                         and no rate-limit headers (forwardError builds a fresh response)
            anthropic -> {"type":"error","error":{"type":"rate_limit_error"|"overloaded_error"|"api_error","message":...}}
  MSG       inner Copilot message (default: a 5-hour-session-limit text from community reports)
  CODE      inner Copilot code (default user_global_rate_limited:pro_plus)
  RETRY_AFTER  if set, send `retry-after: <value>` with the failure (what a header-preserving
               proxy, or a shim that parses the wait out of MSG, would send)
  EXTRA_HDR "name:value" header added to failures (e.g. x-should-retry:false)
Usage: python3 fake_copilot.py <port> <log.jsonl>
"""
import itertools
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, LOG = int(sys.argv[1]), sys.argv[2]
FAIL = os.environ.get("FAIL", "none")
K = int(os.environ.get("K", "0"))
WINDOW = float(os.environ.get("WINDOW", "0"))
STATUS = int(os.environ.get("STATUS", "429"))
FMT = os.environ.get("FMT", "copilot")
MSG = os.environ.get("MSG", "Sorry, you've exceeded your 5 hour session limits. "
                            "Please wait 5 hours 52 minutes before trying again.")
CODE = os.environ.get("CODE", "user_global_rate_limited:pro_plus")
RETRY_AFTER = os.environ.get("RETRY_AFTER")
# EXTRA_HDR="name:value" adds one more header to failures (e.g. x-should-retry:false)
EXTRA_HDR = os.environ.get("EXTRA_HDR")
MESSAGES_SUFFIX = "/v1/" + "messages"
ids = itertools.count(1)
lock = threading.Lock()
state = {"n": 0, "t0": None}


def sse(text):
    n = next(ids)
    ev = [
        ("message_start", {"type": "message_start", "message": {
            "id": f"msg_{n}", "type": "message", "role": "assistant", "model": "fake", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"input_tokens": 100, "output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in ev).encode()


def error_body():
    if FMT == "anthropic":
        etype = {429: "rate_limit_error", 529: "overloaded_error"}.get(STATUS, "api_error")
        return json.dumps({"type": "error", "error": {"type": etype, "message": MSG}}).encode()
    inner = json.dumps({"error": {"message": MSG, "code": CODE}})
    return json.dumps({"error": {"message": inner, "type": "error"}}).encode()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, data, extra=()):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        for k, v in extra:
            self.send_header(k, v)
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
        path = self.path.split("?")[0]
        now = time.time()
        with lock:
            if state["t0"] is None:
                state["t0"] = now
            state["n"] += 1
            n = state["n"]
            t0 = state["t0"]
        fail = path.endswith(MESSAGES_SUFFIX) and (
            FAIL == "always" or (FAIL == "count" and n <= K) or (FAIL == "window" and now - t0 < WINDOW))
        rec = {"t": round(now, 3), "path": path, "model": body.get("model"),
               "stream": bool(body.get("stream")), "n_messages": len(body.get("messages", []) or []),
               "status": STATUS if fail else 200, "x_retry": self.headers.get("x-stainless-retry-count")}
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
        if fail:
            extra = [("retry-after", RETRY_AFTER)] if RETRY_AFTER else []
            if EXTRA_HDR:
                extra.append(tuple(EXTRA_HDR.split(":", 1)))
            return self._send(STATUS, "application/json", error_body(), extra)
        if path.endswith("/count_tokens"):
            return self._send(200, "application/json", b'{"input_tokens": 100}')
        if body.get("stream"):
            return self._send(200, "text/event-stream", sse("ok."))
        out = {"id": f"msg_{next(ids)}", "type": "message", "role": "assistant", "model": "fake",
               "content": [{"type": "text", "text": "ok."}], "stop_reason": "end_turn", "stop_sequence": None,
               "usage": {"input_tokens": 100, "output_tokens": 5}}
        self._send(200, "application/json", json.dumps(out).encode())


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
