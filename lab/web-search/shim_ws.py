"""Prototype: copilot_shim plus a local answer for Anthropic server-tool sub-requests.

A /v1/messages body whose tools include a server tool (``type`` like ``web_search_20250305``,
``web_fetch_*``, ``code_execution_*``) is not forwarded. Copilot has no server tools, and
copilot-api 0.7.0 turns one into a parameterless function the model can only fake. The shim
answers 400 ``invalid_request_error`` with a plain explanation instead, which Claude Code hands
to the model as an is_error tool result. Everything else goes to the unchanged shim.

Usage: python3 -I shim_ws.py <repo-root> [copilot_shim args, e.g. --port P --upstream URL]
"""
import io
import json
import sys

sys.path.insert(0, sys.argv.pop(1))
from copilot_shim import server  # noqa: E402

SERVER_TOOL_PREFIXES = ("web_search_", "web_fetch_", "code_execution_")
MESSAGE = ("copilot-shim: {name} is an Anthropic server-side tool and GitHub Copilot has no "
           "equivalent, so this request was not sent. Web search is unavailable in this session; "
           "use WebFetch on a known URL, or tell the user.")


def server_tools(payload):
    return [t for t in payload.get("tools") or []
            if isinstance(t, dict) and str(t.get("type", "")).startswith(SERVER_TOOL_PREFIXES)]


_orig = server.make_handler


def make_handler(*a, **kw):
    base = _orig(*a, **kw)

    class Handler(base):
        def _forward(self):
            if self.command == "POST" and self.path.split("?", 1)[0].rstrip("/") == "/v1/messages":
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    payload = json.loads(raw)
                except ValueError:
                    payload = None
                st = server_tools(payload) if isinstance(payload, dict) else []
                if st:
                    name = st[0].get("name") or st[0].get("type")
                    server.log.info("/v1/messages server_tool=%s answered locally (not sent)",
                                    st[0].get("type"))
                    msg = json.dumps({"type": "error", "error": {
                        "type": "invalid_request_error", "message": MESSAGE.format(name=name)}}).encode()
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(msg)))
                    self.end_headers()
                    self.wfile.write(msg)
                    return
                self.rfile = io.BufferedReader(io.BytesIO(raw))  # body back for the normal path
            return super()._forward()

    for verb in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        setattr(Handler, "do_" + verb, Handler._forward)
    return Handler


server.make_handler = make_handler
sys.exit(server.main(sys.argv[1:]))
