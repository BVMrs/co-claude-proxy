"""Run all relay experiments and print a JSON summary.  python3 lab/sse-relay/run.py [E1 E2 ...]"""

from __future__ import annotations

import json
import random
import socket
import statistics
import sys
import threading
import time

from harness import (MSG_PATH, SSE_HEAD_CHUNKED, SSE_HEAD_CLOSE, Client, Upstream, capture_stderr, chunk, now,
                     request_bytes, sse_event, start_shim)


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def ms(x):
    return round(x * 1000, 2)


# ------------------------------------------------------------------ E1 latency
def sse_script(n, gap_fn, framing="chunked", per_write=1):
    """Stream n events; gap_fn(i) seconds before event i. Records upstream send times."""
    def script(s, reqline, hdrs, body, rec):
        s.sendall(SSE_HEAD_CHUNKED if framing == "chunked" else SSE_HEAD_CLOSE)
        rec["sent"] = []
        for i in range(n):
            g = gap_fn(i)
            if g:
                time.sleep(g)
            ev = sse_event(i)
            s.sendall(chunk(ev) if framing == "chunked" else ev)
            rec["sent"].append(now())
        if framing == "chunked":
            s.sendall(b"0\r\n\r\n")
        rec["end"] = now()
        return framing == "chunked"
    return script


PATTERNS = {
    # Claude token deltas are typically 10-50 ms apart; copilot-api re-emits each as one SSE event.
    "steady_20ms": lambda i: 0.020,
    "steady_5ms": lambda i: 0.005,
    # Event pairs (e.g. content_block_stop + content_block_start, or delta + ping) 0.3 ms apart, 30 ms between pairs.
    "pairs_0.3ms_then_30ms": lambda i: 0.0003 if i % 2 else 0.030,
    # Bursts of 4 back-to-back events every 50 ms (upstream batching).
    "burst4_every_50ms": lambda i: 0.0 if i % 4 else 0.050,
}


def one_stream(port_or_upstream, up, n):
    c = Client(port_or_upstream)
    r = c.roundtrip(request_bytes(), timeout=30)
    c.close()
    rec = up.records[-1]
    lat = [a - s for a, s in zip(r.event_times, rec["sent"])]
    return r, lat, r.end_time - rec["end"]


def E1(n=200, reps=3):
    out = {}
    for name, fn in PATTERNS.items():
        for framing in ("chunked", "close"):
            up = Upstream(sse_sse := sse_script(n, fn, framing))
            shim, sp = start_shim(up.url)
            shim_nd, sp_nd = start_shim(up.url, nodelay=True)
            row = {}
            for label, port in (("direct", up.port), ("shim", sp), ("shim+nodelay", sp_nd)):
                lats, ends, ok = [], [], 0
                for _ in range(reps):
                    r, lat, end = one_stream(port, up, n)
                    ok += r.complete and len(lat) == n
                    lats += lat
                    ends.append(end)
                row[label] = {"ok": f"{ok}/{reps}", "p50_ms": ms(pct(lats, 50)), "p99_ms": ms(pct(lats, 99)),
                              "max_ms": ms(max(lats)), "n_over_10ms": sum(x > 0.010 for x in lats),
                              "events": len(lats), "end_of_stream_ms_max": ms(max(ends))}
            out[f"{name}/{framing}"] = row
            shim.shutdown(); shim_nd.shutdown(); up.close()
    return out


# ------------------------------------------------------------------ E2 fidelity
def E2(trials=300, seed=7):
    rng = random.Random(seed)
    alphabet = "abc xyz é漢字🙂\\\"{}\n"
    fails = []
    stats = {"trials": 0, "bytes": 0, "max_event": 0}
    for framing in ("chunked", "close"):
        for t in range(trials // 2):
            events = []
            for _ in range(rng.randint(1, 40)):
                size = int(min(300_000, rng.lognormvariate(4.5, 2.0)))
                txt = "".join(rng.choice(alphabet) for _ in range(size))
                events.append(f"event: e\ndata: {json.dumps(txt, ensure_ascii=False)}\n\n".encode())
            payload = b"".join(events)
            # re-split at random byte offsets (may cut a UTF-8 char or an event in half)
            cuts = sorted(rng.sample(range(1, len(payload)), min(len(payload) - 1, rng.randint(0, 30))))
            pieces = [payload[a:b] for a, b in zip([0] + cuts, cuts + [len(payload)])]
            if framing == "chunked" and rng.random() < 0.3:
                pieces.insert(rng.randrange(len(pieces) + 1), b"")  # never sent as a 0-chunk (would end stream)

            def script(s, reqline, hdrs, body, rec, pieces=pieces, framing=framing):
                s.sendall(SSE_HEAD_CHUNKED if framing == "chunked" else SSE_HEAD_CLOSE)
                for p in pieces:
                    if not p:
                        continue
                    if rng.random() < 0.2:
                        time.sleep(0.001)
                    s.sendall(chunk(p) if framing == "chunked" else p)
                if framing == "chunked":
                    s.sendall(b"0\r\n\r\n")
                return framing == "chunked"

            up = Upstream(script)
            shim, sp = start_shim(up.url)
            c = Client(sp)
            r = c.roundtrip(request_bytes(), timeout=30)
            c.close()
            shim.shutdown(); up.close()
            stats["trials"] += 1
            stats["bytes"] += len(payload)
            stats["max_event"] = max(stats["max_event"], max(map(len, events)))
            if not r.complete or r.body != payload:
                fails.append({"framing": framing, "trial": t, "complete": r.complete, "error": r.error,
                              "got": len(r.body), "want": len(payload)})
    stats["failures"] = len(fails)
    stats["examples"] = fails[:5]
    return stats


# ------------------------------------------------------------------ E3 keep-alive
def E3():
    def script(s, reqline, hdrs, body, rec):
        method, path, _ = reqline.split()
        kind = path.rsplit("/", 1)[-1]
        if kind == "sse":
            s.sendall(SSE_HEAD_CHUNKED + b"".join(chunk(sse_event(i)) for i in range(5)) + b"0\r\n\r\n")
        elif kind == "json":
            d = b'{"ok":true}'
            s.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(d), d))
        elif kind == "err":
            d = b'{"error":{"message":"This model does not support assistant message prefill"}}'
            s.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(d), d))
        elif kind == "err_chunked":
            d = b'{"error":{"message":"boom"}}'
            s.sendall(b"HTTP/1.1 500 Internal\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
                      + chunk(d) + b"0\r\n\r\n")
        elif kind == "nocontent":
            s.sendall(b"HTTP/1.1 204 No Content\r\n\r\n")
        elif kind == "head":
            s.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n")
        return True

    up = Upstream(script)
    shim, sp = start_shim(up.url)
    c = Client(sp)
    seq = ["sse", "json", "err", "sse", "nocontent", "sse", "err_chunked", "json", "head", "sse"] * 3
    results = []
    for k in seq:
        method = "HEAD" if k == "head" else ("GET" if k == "nocontent" else "POST")
        body = b"" if method != "POST" else b"{}"
        r = c.roundtrip(request_bytes(method, f"/x/{k}", body), head_only=(method == "HEAD"), timeout=5)
        results.append((k, r.status, r.complete, r.error))
    c.close()
    shim.shutdown(); up.close()
    ok = sum(1 for k, st, comp, err in results if comp and not err)
    return {"requests_on_one_client_conn": len(seq), "ok": ok, "upstream_tcp_connections": up.connections,
            "failures": [x for x in results if not (x[2] and not x[3])][:5]}


# ------------------------------------------------------------------ E4 client abort
def E4():
    out = {}
    for gap in (0.02, 0.5):
        def script(s, reqline, hdrs, body, rec, gap=gap):
            s.sendall(SSE_HEAD_CHUNKED)
            rec["closed_at"] = None
            s.settimeout(0)
            i = 0
            t_end = now() + 15
            while now() < t_end:
                try:
                    s.sendall(chunk(sse_event(i)))
                except OSError:
                    rec["closed_at"] = now(); rec["how"] = "send failed"; return False
                i += 1
                # wait `gap`, but notice a FIN from the shim straight away
                t_next = now() + gap
                while now() < t_next:
                    try:
                        if s.recv(1) == b"":
                            rec["closed_at"] = now(); rec["how"] = "FIN"; rec["sent_after"] = i; return False
                    except BlockingIOError:
                        pass
                    except OSError:
                        rec["closed_at"] = now(); rec["how"] = "RST"; return False
                    time.sleep(0.001)
            rec["how"] = "still open after 15 s"
            return False

        up = Upstream(script)
        shim, sp = start_shim(up.url)
        with capture_stderr() as err:
            c = Client(sp)
            c.roundtrip(request_bytes(), stop_after_events=5, timeout=5)
            t_close = now()
            c.close()
            deadline = now() + 16
            while now() < deadline and "how" not in up.records[-1]:
                time.sleep(0.005)
            time.sleep(0.2)
        rec = up.records[-1]
        out[f"upstream_gap_{int(gap*1000)}ms"] = {
            "upstream_sees_close_after_ms": ms(rec["closed_at"] - t_close) if rec.get("closed_at") else None,
            "how": rec.get("how"),
            "stderr_traceback": "Traceback" in err.getvalue(),
            "stderr_last_line": (err.getvalue().strip().splitlines() or [""])[-1][:160],
        }
        shim.shutdown(); up.close()
    return out


# ------------------------------------------------------------------ E5 upstream failure mid-stream
def E5():
    def abort_chunked(s, reqline, hdrs, body, rec):
        s.sendall(SSE_HEAD_CHUNKED + b"".join(chunk(sse_event(i)) for i in range(3)))
        time.sleep(0.05)
        return False  # close without 0-chunk

    def abort_close(s, reqline, hdrs, body, rec):
        s.sendall(SSE_HEAD_CLOSE + b"".join(sse_event(i) for i in range(3)))
        time.sleep(0.05)
        return False

    def abort_before_headers(s, reqline, hdrs, body, rec):
        return False

    def rst_mid_stream(s, reqline, hdrs, body, rec):
        s.sendall(SSE_HEAD_CHUNKED + chunk(sse_event(0)))
        time.sleep(0.05)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
        return False

    def stall_mid_stream(s, reqline, hdrs, body, rec):
        s.sendall(SSE_HEAD_CHUNKED + chunk(sse_event(0)))
        time.sleep(3)
        return False

    def stall_before_headers(s, reqline, hdrs, body, rec):
        time.sleep(3)
        return False

    cases = {
        "chunked_close_without_terminator": (abort_chunked, 600),
        "close_delimited_eof_after_3_events": (abort_close, 600),
        "close_before_headers": (abort_before_headers, 600),
        "rst_mid_stream": (rst_mid_stream, 600),
        "stall_mid_stream_shim_timeout_1s": (stall_mid_stream, 1.0),
        "stall_before_headers_shim_timeout_1s": (stall_before_headers, 1.0),
    }
    out = {}
    for name, (script, timeout) in cases.items():
        up = Upstream(script)
        shim, sp = start_shim(up.url, timeout=timeout)
        with capture_stderr() as err:
            c = Client(sp)
            t0 = now()
            r = c.roundtrip(request_bytes(), timeout=10)
            c.close()
            time.sleep(0.1)
        out[name] = {"status": r.status, "events": len(r.event_times), "client_complete": r.complete,
                     "client_error": r.error or None, "elapsed_ms": ms(r.end_time - t0),
                     "stderr_traceback": "Traceback" in err.getvalue(),
                     "stderr_exc": next((ln for ln in reversed(err.getvalue().splitlines())
                                         if "Error" in ln or "Incomplete" in ln), "")[:160]}
        shim.shutdown(); up.close()
    return out


# ------------------------------------------------------------------ E6 request framing
def E6():
    seen = []

    def script(s, reqline, hdrs, body, rec):
        seen.append((reqline, hdrs.get("content-length"), hdrs.get("transfer-encoding"), body))
        d = b'{"ok":true}'
        s.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(d), d))
        return True

    up = Upstream(script)
    shim, sp = start_shim(up.url)
    out = {}
    # (a) chunked request body (fetch() with a ReadableStream body does this; a string body does not)
    body = b'{"model":"claude-opus-5.5","messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"{"}]}'
    req = (f"POST {MSG_PATH} HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
           f"Transfer-Encoding: chunked\r\n\r\n").encode() + chunk(body) + b"0\r\n\r\n"
    c = Client(sp)
    r = c.roundtrip(req, timeout=3)
    r2 = c.roundtrip(request_bytes("GET", "/v1/models", b""), timeout=3)
    c.close()
    time.sleep(0.1)
    out["chunked_request_body"] = {
        "client_status": r.status, "upstream_saw": [(a, cl, te, len(b)) for a, cl, te, b in seen],
        "next_request_on_same_conn": {"status": r2.status, "error": r2.error or None},
    }
    shim.shutdown(); up.close()
    return out


# ------------------------------------------------------------------ E1b Nagle stress (added after E1 showed one 40 ms stall)
def E1b(n=200, reps=10):
    out = {}
    pats = {"burst4_every_50ms": PATTERNS["burst4_every_50ms"],
            "pairs_0.3ms_then_30ms": PATTERNS["pairs_0.3ms_then_30ms"],
            "burst2_0.05ms_every_20ms": lambda i: 0.00005 if i % 2 else 0.020}
    for name, fn in pats.items():
        for framing in ("chunked", "close"):
            up = Upstream(sse_script(n, fn, framing))
            row = {}
            for label, nd in (("shim", False), ("shim+nodelay", True)):
                shim, sp = start_shim(up.url, nodelay=nd)
                lats = []
                for _ in range(reps):
                    _, lat, _ = one_stream(sp, up, n)
                    lats += lat
                shim.shutdown()
                stalls = [x for x in lats if x > 0.010]
                row[label] = {"events": len(lats), "p99_ms": ms(pct(lats, 99)), "max_ms": ms(max(lats)),
                              "stalls_over_10ms": len(stalls),
                              "stall_ms": sorted(ms(x) for x in stalls)[:8]}
            out[f"{name}/{framing}"] = row
            up.close()
    return out


if __name__ == "__main__":
    which = sys.argv[1:] or ["E1", "E1b", "E2", "E3", "E4", "E5", "E6"]
    res = {}
    for w in which:
        t = time.time()
        res[w] = globals()[w]()
        res[w + "_seconds"] = round(time.time() - t, 1)
        print(w, json.dumps(res[w], ensure_ascii=False, indent=1), flush=True)
    with open(f"results-{'-'.join(which)}.json", "w") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)

