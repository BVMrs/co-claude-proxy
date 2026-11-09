import time, run
from harness import *
# E4-style abort, print the full stderr
def script(s, reqline, hdrs, body, rec):
    s.sendall(SSE_HEAD_CHUNKED)
    for i in range(100):
        try: s.sendall(chunk(sse_event(i)))
        except OSError: return False
        time.sleep(0.02)
    s.sendall(b"0\r\n\r\n"); return False
up = Upstream(script); shim, sp = start_shim(up.url)
with capture_stderr() as err:
    c = Client(sp); c.roundtrip(request_bytes(), stop_after_events=5); c.close(); time.sleep(0.3)
print("== client abort ==\n", err.getvalue()[-900:])
def stall(s, reqline, hdrs, body, rec):
    s.sendall(SSE_HEAD_CHUNKED + chunk(sse_event(0))); time.sleep(2.5); return False
up2 = Upstream(stall); shim2, sp2 = start_shim(up2.url, timeout=1.0)
with capture_stderr() as err:
    c = Client(sp2); r = c.roundtrip(request_bytes()); c.close(); time.sleep(2.0)
print("== stall ==", r.error, "\n", err.getvalue()[-900:])
