"""HTTP pass-through proxy that applies :func:`copilot_shim.rewrite.rewrite`.

Run it between Claude Code and copilot-api::

    claude  ->  shim (:4142)  ->  copilot-api (:4141)  ->  api.githubcopilot.com

Everything is forwarded unchanged except JSON POST bodies for the message
endpoints, which are rewritten. Responses (including SSE streams) are relayed
as they arrive. Every rewritable request is logged with its model and the role
of its last message, which is the data the handover asks to capture.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .rewrite import DEFAULT_PREFILL_OK, MODES, Policy, rewrite

log = logging.getLogger("copilot_shim")

ANTHROPIC_PATHS = ("/v1/messages", "/v1/messages/count_tokens")
REWRITE_PATHS = (
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/chat/completions",
    "/chat/completions",
)
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


def make_handler(upstream: str, policy: Policy, timeout: float = 600.0):
    parsed = urllib.parse.urlsplit(upstream)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"bad upstream URL: {upstream!r}")
    base_path = parsed.path.rstrip("/")
    conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "copilot-shim"

        def log_message(self, fmt, *args):  # route through logging
            log.debug("%s " + fmt, self.address_string(), *args)

        def _send_error_json(self, status: int, message: str) -> None:
            msg = json.dumps({"type": "error", "error": {"type": "api_error", "message": message}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

        def _forward(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            path_only = self.path.split("?", 1)[0]

            route = path_only.rstrip("/")
            if self.command == "POST" and route in REWRITE_PATHS and body:
                try:
                    payload = json.loads(body)
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    res = rewrite(payload, policy, anthropic=route in ANTHROPIC_PATHS)
                    model_note = res.model
                    if res.original_model != res.model:
                        model_note = f"{res.original_model}->{res.model}"
                    log.info(
                        "%s model=%s last_role=%s stream=%s action=%s%s%s",
                        path_only, model_note, res.last_role, bool(payload.get("stream")),
                        res.action, f" removed={res.removed}" if res.removed else "",
                        f" system_folded={res.system_folded}" if res.system_folded else "",
                    )
                    if res.action != "none" or res.system_folded or res.original_model != res.model:
                        body = json.dumps(res.body).encode()

            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
            headers["Host"] = parsed.netloc
            if body or self.command in ("POST", "PUT", "PATCH"):
                headers["Content-Length"] = str(len(body))

            conn = conn_cls(parsed.hostname, parsed.port, timeout=timeout)
            try:
                conn.request(self.command, base_path + self.path, body=body or None, headers=headers)
                resp = conn.getresponse()
            except OSError as exc:
                conn.close()
                log.error("upstream %s unreachable: %s", upstream, exc)
                self._send_error_json(502, f"copilot-shim: upstream {upstream} unreachable: {exc}")
                return

            try:
                self._relay(resp, path_only)
            finally:
                conn.close()

        def _relay(self, resp, path_only):
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in HOP_BY_HOP:
                    self.send_header(k, v)

            if self.command == "HEAD" or resp.status in (204, 304):
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if resp.status >= 400:
                data = resp.read()
                log.warning("%s upstream %s: %s", path_only, resp.status,
                            data[:500].decode("utf-8", "replace"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            length = resp.getheader("Content-Length")
            if length is not None and resp.getheader("Transfer-Encoding") is None:
                self.send_header("Content-Length", length)
                self.end_headers()
                self.wfile.write(resp.read())
                return

            # Unknown length (SSE): relay with chunked encoding as data arrives.
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward

    return Handler


def serve(host: str, port: int, upstream: str, policy: Policy) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(upstream, policy))
    server.daemon_threads = True
    return server


def parse_map(items) -> dict[str, str]:
    out = {}
    for item in items or ():
        src, sep, dst = item.partition("=")
        if not sep or not src or not dst:
            raise ValueError(f"--map expects FROM=TO, got {item!r}")
        out[src] = dst
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="python3 -m copilot_shim",
        description="Proxy in front of copilot-api that removes assistant prefill for models that reject it.",
    )
    p.add_argument("--host", default="127.0.0.1", help="listen address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=4142, help="listen port (default 4142)")
    p.add_argument("--upstream", default="http://localhost:4141", help="copilot-api base URL")
    p.add_argument("--mode", choices=MODES, default="strip",
                   help="strip: drop the trailing assistant turn; hint: drop it and ask the model to "
                        "start its reply with that text; off: log only")
    p.add_argument("--prefill-ok", action="append", metavar="GLOB",
                   help="model ID glob that accepts prefill and is passed through unchanged "
                        f"(repeatable; default {', '.join(DEFAULT_PREFILL_OK)}). "
                        "Use --prefill-ok '' to rewrite for every model.")
    p.add_argument("--map", action="append", metavar="FROM=TO", help="rename a model ID (repeatable)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    prefill_ok = DEFAULT_PREFILL_OK if args.prefill_ok is None else tuple(g for g in args.prefill_ok if g)
    try:
        policy = Policy(mode=args.mode, prefill_ok=prefill_ok, model_map=parse_map(args.map))
    except ValueError as exc:
        p.error(str(exc))
    server = serve(args.host, args.port, args.upstream, policy)
    log.info("copilot-shim on http://%s:%d -> %s (mode=%s, prefill_ok=%s)",
             args.host, args.port, args.upstream, policy.mode, ",".join(policy.prefill_ok) or "-")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
