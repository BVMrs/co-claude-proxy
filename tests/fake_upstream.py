"""A stand-in for copilot-api + api.githubcopilot.com with the behaviour from docs/HANDOVER.md.

* ``GET /v1/models`` lists MODELS.
* A model not in MODELS gets ``400 model_not_supported``.
* A model not in PREFILL_OK whose last message is from the assistant gets
  ``400 This model does not support assistant message prefill``.
* Otherwise 200: JSON, or an SSE stream when ``stream`` is true.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = [
    "claude-haiku-4.5", "claude-haiku-5.5", "claude-opus-4.8", "claude-opus-5",
    "claude-opus-5.5", "claude-sonnet-5", "claude-sonnet-5.5", "gpt-5",
]
PREFILL_OK = {"claude-haiku-4.5", "gpt-5"}
PREFILL_ERROR = "This model does not support assistant message prefill"

SSE_EVENTS = [
    ("message_start", {"type": "message_start", "message": {"id": "msg_1", "role": "assistant", "content": []}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}}),
    ("message_stop", {"type": "message_stop"}),
]


class FakeUpstream:
    def __init__(self, broken_models=()):
        self.received: list[tuple[str, str, dict | None, dict]] = []
        self.broken_models = set(broken_models)
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _json(self, status, obj):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.received.append(("GET", self.path, None, dict(self.headers)))
                if self.path == "/v1/models":
                    self._json(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS]})
                else:
                    self._json(404, {"error": {"message": "not found"}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.received.append(("POST", self.path, body, dict(self.headers)))
                model = body.get("model")
                if model in outer.broken_models:
                    self._json(500, {"error": {"message": "boom"}})
                    return
                if model not in MODELS:
                    self._json(400, {"error": {"message": "The requested model is not supported.",
                                               "code": "model_not_supported"}})
                    return
                last_role = body["messages"][-1]["role"]
                if self.path.startswith("/v1/messages"):
                    # copilot-api maps every non-user Anthropic message (including
                    # role "system") to an OpenAI assistant turn (copilot-api#272).
                    ends_on_assistant = last_role != "user"
                else:
                    ends_on_assistant = last_role == "assistant"
                if ends_on_assistant and model not in PREFILL_OK:
                    self._json(400, {"error": {"message": PREFILL_ERROR, "code": "invalid_request_body"}})
                    return
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for name, data in SSE_EVENTS:
                        self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                        self.wfile.flush()
                    self.close_connection = True
                    return
                self._json(200, {"id": "msg_1", "type": "message", "role": "assistant", "model": model,
                                 "content": [{"type": "text", "text": "ok"}]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
