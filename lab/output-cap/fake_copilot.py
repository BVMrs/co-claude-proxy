"""Fake copilot-api (Anthropic side) that enforces a Copilot-style *output* token cap.

No model, no credentials, no network. Copilot's /models lists max_output_tokens per model
(32k-64k for Claude); Claude Code 2.1.321 asks for max_tokens 128000 on the Opus loop. What
real Copilot does with max_tokens > cap is unknown, so MODE picks one behaviour:

  pass        no cap (control)
  reject-cp   400 as copilot-api 0.7.0 forwards it (forwardError: Copilot JSON as a string)
  reject-max  400 Anthropic text "max_tokens: X > CAP, which is the maximum allowed number of
              output tokens for this model"
  reject-ctx  400 Anthropic text "input length and `max_tokens` exceed context limit: I + X > I+CAP"
              (Claude Code parses this one and could retry with a smaller max_tokens)
  clamp       accept; generate at most min(max_tokens, CAP) tokens, then stop_reason max_tokens

The fake model's answer: a main-loop request (has tools) whose user text contains <<LONG k>>
"needs" k output tokens. Each response delivers min(remaining, allowed) virtual tokens (usage
output_tokens), with stop_reason max_tokens while something is left. A later request in the
same turn (last user message is Claude Code's output-limit nudge) continues the remainder.
Everything else gets a short "ok.".
Usage: CAP=32000 MODE=clamp python3 fake_copilot.py <port> <log.jsonl>
"""
import itertools
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, LOG = int(sys.argv[1]), sys.argv[2]
CAP = int(os.environ.get("CAP", "32000"))
MODE = os.environ.get("MODE", "clamp")
ids = itertools.count(1)
state = {"remaining": 0, "part": 0}
NUDGES = ("Output token limit hit", "cut off because it exceeded the output token limit",
          "reached the output limit")


def text_of(content):
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                out.append(b.get("text", ""))
            elif b.get("type") == "tool_result":
                out.append(text_of(b.get("content")))
    return "\n".join(out)


def sse(text, n_in, n_out, stop):
    n = next(ids)
    ev = [
        ("message_start", {"type": "message_start", "message": {
            "id": f"msg_{n}", "type": "message", "role": "assistant", "model": "fake", "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                           "usage": {"input_tokens": n_in, "output_tokens": n_out}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in ev).encode()


def error_body(n_in, max_tokens):
    if MODE == "reject-max":
        msg = f"max_tokens: {max_tokens} > {CAP}, which is the maximum allowed number of output tokens for this model"
        return json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": msg}}).encode()
    if MODE == "reject-ctx":
        msg = f"input length and `max_tokens` exceed context limit: {n_in} + {max_tokens} > {n_in + CAP}, decrease input length or `max_tokens` and try again"
        return json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": msg}}).encode()
    # reject-cp: plausible Copilot text (unconfirmed), wrapped the way copilot-api 0.7.0 does it
    inner = json.dumps({"error": {
        "message": f"max_tokens is too large: {max_tokens}. This model supports at most {CAP} completion tokens.",
        "code": "invalid_request_body"}})
    return json.dumps({"error": {"message": inner, "type": "error"}}).encode()


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
        path = self.path.split("?")[0]
        n_in = len(raw) // 4
        msgs = body.get("messages") or []
        users = [m for m in msgs if isinstance(m, dict) and m.get("role") == "user"]
        last_text = text_of(users[-1].get("content")) if users else ""
        mt = body.get("max_tokens")
        main = bool(body.get("tools"))
        rec = {"path": path, "model": body.get("model"), "max_tokens": mt, "main": main,
               "n_messages": len(msgs), "bytes": len(raw)}
        if path.endswith("/count_tokens"):
            rec["status"] = 200
            self._log(rec)
            return self._send(200, "application/json", json.dumps({"input_tokens": n_in}).encode())
        if MODE.startswith("reject") and isinstance(mt, int) and mt > CAP:
            rec["status"] = 400
            self._log(rec)
            return self._send(400, "application/json", error_body(n_in, mt))
        allowed = mt if isinstance(mt, int) else CAP
        if MODE == "clamp":
            allowed = min(allowed, CAP)
        m = re.search(r"<<LONG (\d+)>>", last_text)
        nudge = any(s in last_text for s in NUDGES)
        if main and m and not nudge:
            state["remaining"], state["part"] = int(m.group(1)), 0
            rec["kind"] = "long-start"
        elif main and nudge and state["remaining"] > 0:
            rec["kind"] = "continue"
            rec["nudge"] = next(s for s in NUDGES if s in last_text)
        else:
            rec["kind"] = "short"
        if rec["kind"] == "short":
            text, n_out, stop = "ok.", 20, "end_turn"
        else:
            state["part"] += 1
            n_out = min(state["remaining"], allowed)
            state["remaining"] -= n_out
            stop = "max_tokens" if state["remaining"] > 0 else "end_turn"
            text = f"[part {state['part']}: {n_out} tokens]" + (" DONE" if stop == "end_turn" else "")
        rec.update(status=200, n_out=n_out, stop=stop, remaining=state["remaining"])
        self._log(rec)
        if body.get("stream"):
            return self._send(200, "text/event-stream", sse(text, n_in, n_out, stop))
        out = {"id": f"msg_{next(ids)}", "type": "message", "role": "assistant", "model": "fake",
               "content": [{"type": "text", "text": text}], "stop_reason": stop, "stop_sequence": None,
               "usage": {"input_tokens": n_in, "output_tokens": n_out}}
        self._send(200, "application/json", json.dumps(out).encode())

    def _log(self, rec):
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
