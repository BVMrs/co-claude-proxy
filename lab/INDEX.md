# Lab index

- 2026-11-09 · rewrite-fuzz · 6000/6000 well-formed requests rewrite to valid API bodies; 4.1% of 12k malformed bodies crash rewrite() (4 root causes), and the server then drops the connection without forwarding
- 2026-11-09 · sse-relay · relay is transparent on chunked upstream (+0.1 ms/event, 300/300 byte-exact, keep-alive 30/30); edge defects: client abort detected only after ~2 upstream gaps and logs a traceback, mid-stream timeout logged nowhere at INFO, chunked request bodies silently dropped, rare 40 ms Nagle stall (close-delimited only)
