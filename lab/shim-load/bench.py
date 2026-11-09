"""Load / large-body benchmark of copilot_shim. Writes results-<exp>.json next to this file.

E1  in-process cost of the shim's body path (json.loads -> rewrite -> json.dumps) vs size
E2  end-to-end added latency per request (shim vs direct), non-streaming, vs size
E3  head-of-line: do big-body rewrites stall *other* in-flight SSE streams?
E4  connection bursts: N fresh connections at once (default backlog 5 vs 128 vs direct)
E5  peak RSS of the shim with K concurrent large bodies in flight

Usage: python3 bench.py E1 E2 E3 E4 E5
Everything is on 127.0.0.1; no credentials, no network.
"""
import json
import os
import socket
import statistics as st
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import bodies  # noqa: E402
from copilot_shim.rewrite import Policy, rewrite  # noqa: E402

PY = sys.executable
UP_PORT, SHIM_PORT = 4301, 4302
MSG_PATH = "/v1/" + "messages"
KB = 1024
SIZES = [50 * KB, 200 * KB, 500 * KB, 1024 * KB, 2048 * KB, 4096 * KB]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


class Proc:
    def __init__(self, args, env=None):
        self.p = subprocess.Popen([PY, *args], stdout=subprocess.PIPE, text=True,
                                  env={**os.environ, **(env or {})})
        line = self.p.stdout.readline()
        assert line.startswith("ready"), line

    def stop(self):
        self.p.kill()
        self.p.wait()


def upstream(**env):
    return Proc([os.path.join(HERE, "upstream.py"), str(UP_PORT)], {k: str(v) for k, v in env.items()})


def shim(backlog=None):
    args = [os.path.join(HERE, "shim_runner.py"), str(SHIM_PORT), f"http://127.0.0.1:{UP_PORT}"]
    if backlog:
        args.append(str(backlog))
    return Proc(args)


def post(port, raw, stream=False, sock_out=None):
    """Raw HTTP/1.1 POST; returns (t_connect, t_total, status, nbytes) or events timestamps."""
    t0 = time.monotonic()
    s = socket.create_connection(("127.0.0.1", port), timeout=60)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    t_conn = time.monotonic() - t0
    head = (f"POST {MSG_PATH} HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n").encode()
    s.sendall(head + raw)
    buf, stamps = b"", []
    while True:
        d = s.recv(65536)
        if not d:
            break
        if stream:
            now = time.monotonic()
            stamps += [now] * d.count(b"\n\n")
        buf += d
    s.close()
    status = int(buf.split(b" ", 2)[1]) if buf else 0
    if stream:
        return stamps, status
    return t_conn, time.monotonic() - t0, status, len(buf)


# ---------------------------------------------------------------- E1
def e1():
    out = []
    variants = [("code", {}, s) for s in SIZES] + [
        ("cjk", {"cjk": True}, 1024 * KB), ("image", {"image_kb": 1024}, 1500 * KB),
        ("prefill", {"trailing_assistant": True}, 1024 * KB)]
    pol = Policy()
    for name, kw, size in variants:
        body = bodies.make(size, **kw)
        raw = bodies.encode(body)
        tl, tr, td = [], [], []
        for _ in range(7):
            a = time.perf_counter(); payload = json.loads(raw)
            b = time.perf_counter(); res = rewrite(payload, pol, anthropic=True)
            c = time.perf_counter(); new = json.dumps(res.body).encode()
            d = time.perf_counter()
            tl.append(b - a); tr.append(c - b); td.append(d - c)
        row = {"variant": name, "in_bytes": len(raw), "out_bytes": len(new),
               "growth_pct": round(100 * (len(new) / len(raw) - 1), 1),
               "approx_tokens": len(raw) // 4, "messages": len(body["messages"]),
               "loads_ms": round(1000 * st.median(tl), 2), "rewrite_ms": round(1000 * st.median(tr), 2),
               "dumps_ms": round(1000 * st.median(td), 2), "action": res.action,
               "folded": res.system_folded}
        row["total_ms"] = round(row["loads_ms"] + row["rewrite_ms"] + row["dumps_ms"], 2)
        out.append(row)
        print(row, flush=True)
    return out


# ---------------------------------------------------------------- E2
def e2(reps=15):
    up, sh = upstream(), shim()
    out = []
    try:
        for size in SIZES:
            body = bodies.make(size)
            body["stream"] = False
            raw = bodies.encode(body)
            res = {}
            for port, label in ((UP_PORT, "direct"), (SHIM_PORT, "shim")):
                post(port, raw)  # warm
                ts = [post(port, raw)[1] for _ in range(reps)]
                res[label] = ts
            row = {"in_bytes": len(raw),
                   "direct_ms_p50": round(1000 * st.median(res["direct"]), 2),
                   "shim_ms_p50": round(1000 * st.median(res["shim"]), 2),
                   "shim_ms_p90": round(1000 * pct(res["shim"], 90), 2)}
            row["added_ms_p50"] = round(row["shim_ms_p50"] - row["direct_ms_p50"], 2)
            out.append(row)
            print(row, flush=True)
    finally:
        up.stop(); sh.stop()
    return out


# ---------------------------------------------------------------- E3
def e3():
    """K background streams (events every 20 ms for 3 s); meanwhile a poster sends big
    non-streaming bodies back-to-back through the same server. Measure the worst
    inter-event gap seen by the streams."""
    K, EVENTS, GAP, REPS = 6, 150, 20, 3
    small = bodies.encode(bodies.make(20 * KB))  # streaming, tiny
    out = []
    cells = ((0, 0, 0), (1024 * KB, 1, 0), (4096 * KB, 1, 0), (4096 * KB, 3, 0),
             (4096 * KB, 1, 0.5), (4096 * KB, 3, 0.5))
    for rep in range(REPS):
        for big_size, posters, every in cells:
            bodyfile = os.path.join(HERE, ".big.json")
            if big_size:
                b = bodies.make(big_size); b["stream"] = False
                with open(bodyfile, "wb") as f:
                    f.write(bodies.encode(b))
            for label in ("direct", "shim"):
                up = upstream(EVENTS=EVENTS, GAP=GAP)
                sh = shim() if label == "shim" else None
                port = SHIM_PORT if sh else UP_PORT
                results = []

                def stream():
                    stamps, status = post(port, small, stream=True)
                    results.append(stamps)

                ts = [threading.Thread(target=stream) for _ in range(K)]
                for t in ts:
                    t.start()
                pp = None
                if posters:
                    time.sleep(0.2)
                    pp = subprocess.Popen([PY, os.path.join(HERE, "poster.py"), str(port), bodyfile,
                                           str(posters), str(every), str(EVENTS * GAP / 1000 - 0.4)],
                                          stdout=subprocess.PIPE, text=True)
                for t in ts:
                    t.join()
                nbig = int(pp.communicate()[0]) if pp else 0
                # first gap skipped: event 0 can trail the response headers
                gaps = [1000 * (b - a) - GAP for s_ in results for a, b in zip(s_[1:], s_[2:])]
                row = {"rep": rep, "big_bytes": big_size, "posters": posters, "pause_s": every,
                       "path": label, "big_requests": nbig, "events": sum(len(s_) for s_ in results),
                       "extra_gap_ms_p50": round(st.median(gaps), 2),
                       "extra_gap_ms_p99": round(pct(gaps, 99), 2),
                       "extra_gap_ms_max": round(max(gaps), 2),
                       "gaps_over_10ms": sum(g > 10 for g in gaps), "gaps_total": len(gaps),
                       "loadavg1": round(os.getloadavg()[0], 2)}
                if sh:
                    sh.stop()
                up.stop()
                out.append(row)
                print(row, flush=True)
    return out


# ---------------------------------------------------------------- E4
def e4():
    """N clients connect at the same instant (barrier) and send a small request;
    upstream holds each for 300 ms. A dropped SYN shows up as connect() >= ~1 s."""
    raw = bodies.encode({"model": "claude-opus-5.5", "max_tokens": 1,
                         "messages": [{"role": "user", "content": "hi"}]})
    big = bodies.make(4096 * KB); big["stream"] = False
    bigraw = bodies.encode(big)
    out = []
    for target in ("direct", "shim", "shim-backlog128", "shim+bigbody"):
        for n in (8, 16, 32, 64):
            up = upstream(DELAY=300)
            sh = None
            if target.startswith("shim"):
                sh = shim(backlog=128 if target == "shim-backlog128" else None)
            port = SHIM_PORT if sh else UP_PORT
            for _ in range(3):
                post(port, raw)
            conns, totals, statuses = [], [], []
            bar = threading.Barrier(n + (1 if target == "shim+bigbody" else 0))

            def client():
                bar.wait()
                if target == "shim+bigbody":
                    time.sleep(0.005)  # let the big body land first
                try:
                    c, t, s, _ = post(port, raw)
                except OSError:
                    c, t, s = float("nan"), float("nan"), -1
                conns.append(c); totals.append(t); statuses.append(s)

            def bigclient():
                bar.wait()
                post(port, bigraw)

            ts = [threading.Thread(target=client) for _ in range(n)]
            if target == "shim+bigbody":
                ts.append(threading.Thread(target=bigclient))
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            if sh:
                sh.stop()
            up.stop()
            row = {"target": target, "n": n, "ok": statuses.count(200),
                   "connect_ms_p50": round(1000 * st.median(conns), 1),
                   "connect_ms_max": round(1000 * max(conns), 1),
                   "connect_over_900ms": sum(c > 0.9 for c in conns),
                   "total_ms_p50": round(1000 * st.median(totals), 1),
                   "total_ms_max": round(1000 * max(totals), 1)}
            out.append(row)
            print(row, flush=True)
    return out


# ---------------------------------------------------------------- E5
def sample_rss(pid, stop, out):
    """Current RSS (KiB) of pid via ps, every 20 ms. (ru_maxrss is useless here: Linux
    carries the parent's peak across fork+exec, so the shim would inherit the bench's.)"""
    while not stop.is_set():
        r = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True)
        if r.stdout.strip():
            out.append(int(r.stdout))
        stop.wait(0.02)


def e5():
    out = []
    for size in (1024 * KB, 4096 * KB):
        b = bodies.make(size); b["stream"] = False
        raw = bodies.encode(b)
        for k in (1, 4, 8, 16):
            up = upstream(DELAY=1500)
            sh = shim()
            post(SHIM_PORT, bodies.encode({"model": "m", "messages": [{"role": "user", "content": "hi"}]}))
            time.sleep(0.1)
            idle = [int(subprocess.run(["ps", "-o", "rss=", "-p", str(sh.p.pid)],
                                       capture_output=True, text=True).stdout)]
            stop, samples = threading.Event(), []
            mon = threading.Thread(target=sample_rss, args=(sh.p.pid, stop, samples))
            mon.start()
            ts = [threading.Thread(target=post, args=(SHIM_PORT, raw)) for _ in range(k)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            stop.set(); mon.join()
            sh.stop(); up.stop()
            row = {"in_bytes": len(raw), "concurrent": k, "idle_rss_mib": round(idle[0] / 1024, 1),
                   "peak_rss_mib": round(max(samples) / 1024, 1)}
            row["per_request_mib"] = round((row["peak_rss_mib"] - row["idle_rss_mib"]) / k, 1)
            row["per_request_x_body"] = round(row["per_request_mib"] * 1024 * 1024 / len(raw), 1)
            out.append(row)
            print(row, flush=True)
    return out


if __name__ == "__main__":
    for exp in sys.argv[1:]:
        t = time.time()
        res = globals()[exp.lower()]()
        meta = {"python": sys.version.split()[0], "cpus": os.cpu_count(), "secs": round(time.time() - t, 1)}
        with open(os.path.join(HERE, f"results-{exp}.json"), "w") as f:
            json.dump({"meta": meta, "rows": res}, f, indent=1)
