"""Run the real copilot_shim server (default Policy: mode=strip) in this process.
Usage: python3 shim_runner.py <port> <upstream_url> [backlog]
backlog: optional override of request_queue_size (default: the shim's, i.e. 5)."""
import logging
import os
import sys
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from copilot_shim.rewrite import Policy  # noqa: E402
from copilot_shim.server import make_handler, serve  # noqa: E402

logging.basicConfig(level=logging.WARNING)
port, up = int(sys.argv[1]), sys.argv[2]
if len(sys.argv) > 3:
    class S(ThreadingHTTPServer):
        request_queue_size = int(sys.argv[3])
        daemon_threads = True
    srv = S(("127.0.0.1", port), make_handler(up, Policy()))
else:
    srv = serve("127.0.0.1", port, up, Policy())


print("ready", srv.request_queue_size, flush=True)
srv.serve_forever()
