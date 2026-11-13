"""Fake Anthropic Messages endpoint (copied from lab/tool-search, plus a web-search sub-request mode).

WS_MODE (env) decides how a request whose tools include a server tool `web_search_*` is answered:
  anthropic : server_tool_use + web_search_tool_result + cited text (what api.anthropic.com returns)
  text      : plain text "results" (copilot-api: model answered without calling the tool)
  tooluse   : a client tool_use named web_search (copilot-api: model called the translated function)
  err400    : 400 invalid_request_error (Copilot rejects the translated tool)


PLAN trigger: a user text containing  PLAN: Tool {json} ;; Tool {json} ;; ...
makes the fake model call those tools one per turn, in order, then answer "done.".
Original description:
Fake Anthropic Messages endpoint for an *interactive* Claude Code session.

Records every request body (one JSON line each) and answers with canned SSE. No model,
no credentials, no network. Triggers in the last user text (main loop only, i.e. a
request that carries tools):
  READ_FILE:<path>  -> one Read tool_use
  AGENT:<text>      -> one Agent tool_use (subagent_type Explore)
  SLOW              -> 60 text deltas, 0.25 s apart (so the client can interrupt it)
  DELAY             -> wait 6 s before sending any response byte (interrupt before first token)
  BASH:<cmd>        -> one Bash tool_use running <cmd> (to end of line)
Anything else gets "done.". A request ending in an assistant turn (prefill) is answered
with a continuation that closes a JSON object if the prefill opened one.
Usage: python3 capture_server.py <port> <out.jsonl>
"""
import itertools
import json
import sys
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, OUT = int(sys.argv[1]), sys.argv[2]
ids = itertools.count(1)


def text_of(c):
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text", "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text")


def last_user(body):
    for m in reversed(body.get("messages") or []):
        if m.get("role") == "user":
            c = m.get("content")
            tr = isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
            return text_of(c), tr
    return "", False


def plan_step(msgs):
    """None if no PLAN in this turn; () when the plan is finished; else (name, input)."""
    for k in range(len(msgs) - 1, -1, -1):
        m = msgs[k]
        if m.get("role") == "user" and "PLAN:" in text_of(m.get("content")):
            steps = [s.strip() for s in text_of(m["content"]).split("PLAN:", 1)[1].split(";;")]
            n = sum(1 for x in msgs[k + 1:] if x.get("role") == "assistant")
            if n >= len(steps):
                return ()
            name, _, js = steps[n].partition(" ")
            return name, json.loads(js.strip().splitlines()[0] if js.strip() else "{}")
    return None


def events(blocks, stop, slow=False):
    n = next(ids)
    yield "message_start", {"type": "message_start", "message": {
        "id": f"msg_{n}", "type": "message", "role": "assistant", "model": "fake", "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            yield "content_block_start", {"type": "content_block_start", "index": i,
                                          "content_block": {"type": "text", "text": ""}}
            parts = [f"word{k} " for k in range(60)] if slow else [b["text"]]
            for p in parts:
                yield "content_block_delta", {"type": "content_block_delta", "index": i,
                                              "delta": {"type": "text_delta", "text": p}}
                if slow:
                    time.sleep(0.25)
        elif b["type"] in ("server_tool_use", "web_search_tool_result"):
            cb = dict(b)
            if b["type"] == "server_tool_use":
                cb["input"] = {}
            yield "content_block_start", {"type": "content_block_start", "index": i, "content_block": cb}
            if b["type"] == "server_tool_use":
                yield "content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                    "type": "input_json_delta", "partial_json": json.dumps(b["input"])}}
        else:
            yield "content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}}
            yield "content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(b["input"])}}
        yield "content_block_stop", {"type": "content_block_stop", "index": i}
    yield "message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                            "usage": {"output_tokens": 5}}
    yield "message_stop", {"type": "message_stop"}


def log(rec):
    rec["t"] = time.time()
    with open(OUT, "a") as f:
        f.write(json.dumps(rec) + "\n")


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
        log({"method": "GET", "path": self.path})
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
        log({"method": "POST", "path": self.path, "body": body})
        if "count_tokens" in self.path:
            return self._send(200, "application/json", b'{"input_tokens": 100}')
        if not isinstance(body, dict) or "/messages" not in self.path:
            return self._send(200, "application/json", b"{}")
        msgs = body.get("messages") or []
        text, is_tr = last_user(body)
        blocks, stop, slow = [{"type": "text", "text": "done."}], "end_turn", False
        if msgs and isinstance(msgs[-1], dict) and msgs[-1].get("role") == "assistant":
            pre = text_of(msgs[-1].get("content"))
            blocks = [{"type": "text", "text": '"title": "Fake title"}' if pre.strip().startswith("{") else " done."}]
        plan = plan_step(msgs)
        ws = [t for t in body.get("tools") or [] if str(t.get("type", "")).startswith("web_search")]
        if ws:
            mode = os.environ.get("WS_MODE", "anthropic")
            q = text.split("query:", 1)[-1].strip() if "query:" in text else text
            if mode == "err400":
                return self._send(400, "application/json", json.dumps({"type": "error", "error": {
                    "type": "invalid_request_error",
                    "message": "Invalid schema for function 'web_search': None is not of type 'object'"}}).encode())
            if mode == "anthropic":
                sid = f"srvtoolu_{next(ids)}"
                blocks = [
                    {"type": "server_tool_use", "id": sid, "name": "web_search", "input": {"query": q}},
                    {"type": "web_search_tool_result", "tool_use_id": sid, "content": [
                        {"type": "web_search_result", "url": "https://example.org/a", "title": "Result A",
                         "encrypted_content": "x", "page_age": None},
                        {"type": "web_search_result", "url": "https://example.org/b", "title": "Result B",
                         "encrypted_content": "y", "page_age": None}]},
                    {"type": "text", "text": "Per Result A, the answer is 42."}]
                stop = "end_turn"
            elif mode == "text":
                blocks = [{"type": "text", "text": "I can't browse, but from memory the answer is 42 (see https://example.org/made-up)."}]
            elif mode == "tooluse":
                blocks = [{"type": "tool_use", "id": f"toolu_{next(ids)}", "name": "web_search", "input": {"query": q}}]
                stop = "tool_use"
            plan = None
        if ws:
            pass
        elif plan is not None and body.get("tools"):
            if plan:
                name, inp = plan
                blocks = [{"type": "tool_use", "id": f"toolu_{next(ids)}", "name": name, "input": inp}]
                stop = "tool_use"
        elif body.get("tools") and not is_tr:
            if "READ_FILE:" in text:
                path = text.split("READ_FILE:")[1].split()[0]
                blocks = [{"type": "tool_use", "id": f"toolu_{next(ids)}", "name": "Read",
                           "input": {"file_path": path}}]
                stop = "tool_use"
            elif "AGENT:" in text and any(t.get("name") == "Agent" for t in body["tools"]):
                blocks = [{"type": "tool_use", "id": f"toolu_{next(ids)}", "name": "Agent",
                           "input": {"description": "look around", "prompt": "List the files. AGENTINNER",
                                     "subagent_type": "Explore"}}]
                stop = "tool_use"
            elif "BASH:" in text:
                cmd = text.split("BASH:")[1].split("\n")[0].strip()
                blocks = [{"type": "tool_use", "id": f"toolu_{next(ids)}", "name": "Bash",
                           "input": {"command": cmd, "description": "run"}}]
                stop = "tool_use"
            elif "SLOW" in text:
                slow = True
            elif "DELAY" in text:
                time.sleep(6)
        if not body.get("stream"):
            msg = {"id": f"msg_{next(ids)}", "type": "message", "role": "assistant", "model": body.get("model"),
                   "content": blocks, "stop_reason": stop, "stop_sequence": None,
                   "usage": {"input_tokens": 10, "output_tokens": 5}}
            return self._send(200, "application/json", json.dumps(msg).encode())
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        try:
            for e, d in events(blocks, stop, slow):
                data = f"event: {e}\ndata: {json.dumps(d)}\n\n".encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except OSError:
            log({"event": "client_aborted"})


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
