"""Fake copilot-api for load tests (run as its own process). Reads the whole body, then:
  body with "stream":true near the start -> chunked SSE: EVENTS events every GAP ms
                                            on a fixed schedule (so client-side gaps
                                            measure relay delay, not upstream drift)
  anything else                          -> small JSON after DELAY ms, echoing the
                                            received byte count
Usage: python3 upstream.py <port>   (env: EVENTS, GAP [ms], DELAY [ms])"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EVENTS = int(os.environ.get("EVENTS", "50"))
GAP = float(os.environ.get("GAP", "20")) / 1000
DELAY = float(os.environ.get("DELAY", "0")) / 1000


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if b'"stream":true' not in body[:200] and b'"stream": true' not in body[:200]:
            if DELAY:
                time.sleep(DELAY)
            out = json.dumps({"id": "msg_1", "type": "message", "role": "assistant",
                              "content": [{"type": "text", "text": "ok"}],
                              "usage": {"input_tokens": n // 4, "output_tokens": 1},
                              "received": n}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        t0 = time.monotonic()
        for i in range(EVENTS):
            wait = t0 + i * GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            ev = b'event: content_block_delta\ndata: {"i":%d}\n\n' % i
            self.wfile.write(b"%x\r\n%s\r\n" % (len(ev), ev))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


if __name__ == "__main__":
    class S(ThreadingHTTPServer):
        request_queue_size = 256  # never the bottleneck
        daemon_threads = True
    s = S(("127.0.0.1", int(sys.argv[1])), H)
    print("ready", flush=True)
    s.serve_forever()
