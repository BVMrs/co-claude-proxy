"""End-to-end harness for copilot_shim's response relay (lab 2026-11-09-sse-relay).

A scripted raw-socket upstream (so framing, timing and aborts are exact) sits
behind the real ``copilot_shim.server`` handler; a raw-socket client in front
records when each SSE event arrives. Same process, loopback, monotonic clock.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from copilot_shim.rewrite import Policy  # noqa: E402
from copilot_shim.server import make_handler  # noqa: E402

now = time.monotonic
MSG_PATH = "/v1/" + "messages"


# --------------------------------------------------------------------- upstream
def read_request(sock: socket.socket, pending: bytearray) -> tuple[str, dict, bytes]:
    buf = bytes(pending)
    while b"\r\n\r\n" not in buf:
        d = sock.recv(65536)
        if not d:
            raise ConnectionError("eof before headers")
        buf += d
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    hdrs = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        hdrs[k.strip().lower()] = v.strip()
    n = int(hdrs.get("content-length", 0))
    while len(rest) < n:
        d = sock.recv(65536)
        if not d:
            break
        rest += d
    pending[:] = rest[n:]
    return lines[0], hdrs, rest[:n]


@dataclass
class Upstream:
    """Each request is served by ``script(sock, reqline, hdrs, body, rec)``; return True to keep alive."""
    script: object
    connections: int = 0
    requests: list = field(default_factory=list)
    records: list = field(default_factory=list)

    def __post_init__(self):
        self.lsock = socket.socket()
        self.lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.lsock.bind(("127.0.0.1", 0))
        self.lsock.listen(64)
        self.port = self.lsock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                s, _ = self.lsock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(s,), daemon=True).start()

    def _serve(self, s):
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # Node's http default
        rec = {}
        self.records.append(rec)
        pending = bytearray()
        try:
            while True:
                try:
                    reqline, hdrs, body = read_request(s, pending)
                except (ConnectionError, OSError):
                    return
                self.requests.append((reqline, hdrs, body))
                if not self.script(s, reqline, hdrs, body, rec):
                    return
        finally:
            with contextlib.suppress(OSError):
                s.close()

    def close(self):
        self.lsock.close()


def sse_event(i: int, pad: int = 60) -> bytes:
    data = {"type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": f"{i:06d}" + "x" * pad}}
    return f"event: content_block_delta\ndata: {json.dumps(data)}\n\n".encode()


def chunk(b: bytes) -> bytes:
    return b"%x\r\n%s\r\n" % (len(b), b)


SSE_HEAD_CHUNKED = (b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                    b"Transfer-Encoding: chunked\r\n\r\n")
SSE_HEAD_CLOSE = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"


# ----------------------------------------------------------------------- shims
def start_shim(upstream_url: str, nodelay: bool = False, timeout: float = 600.0):
    base = make_handler(upstream_url, Policy(mode="strip"), timeout=timeout)

    class H(base):
        def setup(self):
            super().setup()
            if nodelay:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    return srv, srv.server_address[1]


# ---------------------------------------------------------------------- client
BODY = json.dumps({"model": "claude-haiku-4.5", "stream": True, "max_tokens": 16,
                   "messages": [{"role": "user", "content": "hi"}]}).encode()


def request_bytes(method="POST", path=MSG_PATH, body=BODY, extra=b"") -> bytes:
    h = (f"{method} {path} HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
         f"Content-Length: {len(body)}\r\n").encode() + extra + b"\r\n"
    return h + body


@dataclass
class Resp:
    status: int = 0
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    event_times: list = field(default_factory=list)  # arrival time of each complete "\n\n" event
    end_time: float = 0.0
    complete: bool = False  # chunked terminator / content-length satisfied / close-delimited EOF
    error: str = ""


class Client:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port))
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # undici/Node default
        self.buf = b""

    def _recv(self) -> bool:
        d = self.sock.recv(65536)
        if not d:
            return False
        self.buf += d
        return True

    def roundtrip(self, req: bytes, head_only=False, stop_after_events=None, timeout=10.0) -> Resp:
        self.sock.settimeout(timeout)
        self.sock.sendall(req)
        r = Resp()
        try:
            return self._read(r, head_only, stop_after_events)
        except OSError as e:
            r.error = f"{type(e).__name__}: {e}"
            return r
        finally:
            r.end_time = now()

    def _read(self, r, head_only, stop_after_events):
        while b"\r\n\r\n" not in self.buf:
            if not self._recv():
                r.error = "eof before headers"
                return r
        head, _, self.buf = self.buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        r.status = int(lines[0].split()[1])
        for ln in lines[1:]:
            k, _, v = ln.partition(":")
            r.headers[k.strip().lower()] = v.strip()
        if head_only or r.status in (204, 304):
            r.complete = True
            return r
        seen = 0

        def note_events():
            nonlocal seen
            c = r.body.count(b"\n\n")
            t = now()
            while seen < c:
                r.event_times.append(t)
                seen += 1

        if r.headers.get("transfer-encoding", "").lower() == "chunked":
            while True:
                while b"\r\n" not in self.buf:
                    if not self._recv():
                        r.error = "eof inside chunked body (no terminator)"
                        return r
                size_line, _, rest = self.buf.partition(b"\r\n")
                size = int(size_line.split(b";")[0], 16)
                while len(rest) < size + 2:
                    if not self._recv():
                        r.error = "eof inside chunk"
                        return r
                    size_line, _, rest = self.buf.partition(b"\r\n")
                if size == 0:
                    self.buf = rest[2:]
                    r.complete = True
                    return r
                r.body += rest[:size]
                self.buf = rest[size + 2:]
                note_events()
                if stop_after_events and seen >= stop_after_events:
                    return r
        elif "content-length" in r.headers:
            n = int(r.headers["content-length"])
            while len(self.buf) < n:
                if not self._recv():
                    r.error = "eof before content-length"
                    return r
            r.body, self.buf = self.buf[:n], self.buf[n:]
            r.complete = True
            return r
        else:  # close-delimited
            while True:
                r.body += self.buf
                self.buf = b""
                note_events()
                if stop_after_events and seen >= stop_after_events:
                    return r
                if not self._recv():
                    r.complete = True  # indistinguishable from truncation
                    return r

    def close(self):
        with contextlib.suppress(OSError):
            self.sock.close()


@contextlib.contextmanager
def capture_stderr():
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        yield buf
    finally:
        sys.stderr = old
