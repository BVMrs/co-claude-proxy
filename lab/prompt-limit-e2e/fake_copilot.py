"""Fake copilot-api (Anthropic side) that enforces a Copilot-style prompt-token cap.

No model, no credentials, no network. Each /v1/messages request is given a *virtual*
prompt size so that a short real session can reach 100k+ tokens:

    virtual = len(body_bytes) // 4 + PAD * (number of "<<PAD>>" markers in messages)

Env knobs:
  CAP      virtual prompt cap (0 = none). Above it the request is rejected.
  ERRFMT   copilot   -> what copilot-api 0.7.0 forwards (src/lib/error.ts forwardError):
                        400 {"error":{"message":"<Copilot JSON as text>","type":"error"}}
           anthropic -> 400 invalid_request_error "prompt is too long: N tokens > CAP maximum"
           commas / codeonly / openai_ctx / copilot413 -> variants, see error_body()
  USAGE    delta  -> copilot-api stream translation when Copilot's first chunk has no usage:
                     message_start input_tokens=0, message_delta input_tokens=N
           start  -> Anthropic-like: message_start input_tokens=N
           none   -> usage lost (Copilot sends usage only in a choices:[] chunk, which
                     copilot-api 0.7.0 drops): input_tokens=0 in both events
  PAD      virtual tokens per marker (default 10000)
A request whose text contains "detailed summary of the conversation" is treated as a
compaction call and answered with a short <summary>.
A request whose messages contain "<<BAD>>" gets an unrelated 400 (invalid_request_body) whose
message quotes the user's text, including Copilot's limit phrase (false-positive probe).
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


def fwd(inner):  # copilot-api 0.7.0 forwardError: Copilot's JSON stringified into error.message
    return json.dumps({"error": {"message": json.dumps(inner), "type": "error"}}).encode()


def error_body(n_in):
    """(status, body) for an over-cap prompt, per ERRFMT."""
    if ERRFMT == "anthropic":
        return 400, json.dumps({"type": "error", "error": {
            "type": "invalid_request_error",
            "message": f"prompt is too long: {n_in} tokens > {CAP} maximum"}}).encode()
    if ERRFMT == "commas":  # same as copilot, numbers with thousands separators
        return 400, fwd({"error": {"message": f"prompt token count of {n_in:,} exceeds the limit of {CAP:,}",
                                   "code": "model_max_prompt_tokens_exceeded"}})
    if ERRFMT == "codeonly":  # code present, no numbers in the text
        return 400, fwd({"error": {"message": "Request too large", "code": "model_max_prompt_tokens_exceeded"}})
    if ERRFMT == "openai_ctx":  # OpenAI's context_length_exceeded wording
        return 400, fwd({"error": {
            "message": f"This model's maximum context length is {CAP} tokens. However, your messages "
                       f"resulted in {n_in} tokens. Please reduce the length of the messages.",
            "type": "invalid_request_error", "code": "context_length_exceeded"}})
    status = 413 if ERRFMT == "copilot413" else 400
    return status, fwd({"error": {"message": f"prompt token count of {n_in} exceeds the limit of {CAP}",
                                  "code": "model_max_prompt_tokens_exceeded"}})


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
        if "<<BAD>>" in msgs:  # unrelated 400 whose message echoes the user's text (see BADTURN in run.sh)
            rec["status"] = 400
            self._log(rec)
            return self._send(400, "application/json", fwd({"error": {
                "message": "Invalid 'messages[0].content': flagged sequence in 'why does copilot say "
                           "prompt token count of 5 exceeds the limit of 4? <<BAD>>'",
                "code": "invalid_request_body"}}))
        if CAP and n_in > CAP:
            st, err = error_body(n_in)
            rec["status"] = st
            self._log(rec)
            return self._send(st, "application/json", err)
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
