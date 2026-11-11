"""Run the real Claude Code binary against fake_copilot.py for every case; write results.json.

Usage: python3 matrix.py <claude-bin> <workdir> [case-prefix ...]
No credentials, no network: throwaway HOME, base URL 127.0.0.1.
"""
import json, os, shutil, subprocess, sys, time

BIN, WORK = os.path.realpath(sys.argv[1]), os.path.realpath(sys.argv[2])
ONLY = sys.argv[3:]
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 4197
ENV32 = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "32000"}
COMPACT = chr(47) + "compact"  # the slash command, sent as a -p prompt

# name, MODE, CAP, model, extra env, list of (prompt, continue?)
CASES = [
    ("pass-opus", "pass", 32000, "claude-opus-5.5", {}, [("hi", False)]),
    ("pass-sonnet", "pass", 32000, "claude-sonnet-5.5", {}, [("hi", False)]),
    ("pass-haiku45", "pass", 32000, "claude-haiku-4.5", {}, [("hi", False)]),
    ("pass-long100k", "pass", 32000, "claude-opus-5.5", {}, [("write <<LONG 100000>>", False)]),
    ("pass-long400k", "pass", 32000, "claude-opus-5.5", {}, [("write <<LONG 400000>>", False)]),
    ("clamp32-long30k", "clamp", 32000, "claude-opus-5.5", {}, [("write <<LONG 30000>>", False)]),
    ("clamp32-long100k", "clamp", 32000, "claude-opus-5.5", {}, [("write <<LONG 100000>>", False)]),
    ("clamp32-long200k", "clamp", 32000, "claude-opus-5.5", {}, [("write <<LONG 200000>>", False)]),
    ("clamp32-long400k", "clamp", 32000, "claude-opus-5.5", {}, [("write <<LONG 400000>>", False)]),
    ("clamp64-long200k", "clamp", 64000, "claude-opus-5.5", {}, [("write <<LONG 200000>>", False)]),
    ("clamp32-env-long100k", "clamp", 32000, "claude-opus-5.5", ENV32, [("write <<LONG 100000>>", False)]),
    ("clamp32-env-long200k", "clamp", 32000, "claude-opus-5.5", ENV32, [("write <<LONG 200000>>", False)]),
    ("reject-cp32", "reject-cp", 32000, "claude-opus-5.5", {}, [("hi", False)]),
    ("reject-cp64", "reject-cp", 64000, "claude-opus-5.5", {}, [("hi", False)]),
    ("reject-max32", "reject-max", 32000, "claude-opus-5.5", {}, [("hi", False)]),
    ("reject-ctx32", "reject-ctx", 32000, "claude-opus-5.5", {}, [("hi", False)]),
    ("reject-ctx32-long100k", "reject-ctx", 32000, "claude-opus-5.5", {}, [("write <<LONG 100000>>", False)]),
    ("reject-cp32-env", "reject-cp", 32000, "claude-opus-5.5", ENV32,
     [("hi", False), ("second turn", True), (COMPACT, True), ("after compact", True)]),
    ("reject-cp32-env-long100k", "reject-cp", 32000, "claude-opus-5.5", ENV32, [("write <<LONG 100000>>", False)]),
    ("reject-cp32-env-sonnet", "reject-cp", 32000, "claude-sonnet-5.5", ENV32, [("hi", False)]),
    # through the real shim plus the max_tokens clamp prototype (proto_shim.py, CAPS claude-*=32000)
    ("shim-reject-cp32", "reject-cp", 32000, "claude-opus-5.5", {"SHIM": "1"},
     [("hi", False), ("second turn", True), (COMPACT, True), ("after compact", True)]),
    ("shim-reject-cp32-long100k", "reject-cp", 32000, "claude-opus-5.5", {"SHIM": "1"}, [("write <<LONG 100000>>", False)]),
    ("shim-reject-cp32-sonnet", "reject-cp", 32000, "claude-sonnet-5.5", {"SHIM": "1"}, [("hi", False)]),
]
SHIM_PORT = 4196


def run(case):
    name, mode, cap, model, extra, turns = case
    d = os.path.join(WORK, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, "home")); os.makedirs(os.path.join(d, "proj"))
    log = os.path.join(d, "log.jsonl"); open(log, "w").close()
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_copilot.py"), str(PORT), log],
                           env={**os.environ, "MODE": mode, "CAP": str(cap)})
    shim = None
    extra = dict(extra)
    base = f"http://127.0.0.1:{PORT}"
    if extra.pop("SHIM", None):
        root = os.path.dirname(os.path.dirname(HERE))
        shim = subprocess.Popen([sys.executable, os.path.join(HERE, "proto_shim.py"), str(SHIM_PORT), base],
                                env={**os.environ, "PYTHONPATH": root, "CAPS": "claude-*=32000"},
                                stderr=open(os.path.join(d, "shim.log"), "w"))
        base = f"http://127.0.0.1:{SHIM_PORT}"
    time.sleep(0.7)
    out = []
    try:
        for prompt, cont in turns:
            with open(log, "a") as f:
                f.write(json.dumps({"turn": prompt}) + "\n")
            env = {"PATH": os.environ["PATH"], "HOME": os.path.join(d, "home"), "TERM": "dumb",
                   "ANTHROPIC_BASE_URL": base, "ANTHROPIC_AUTH_TOKEN": "dummy",
                   "ANTHROPIC_MODEL": model, "ANTHROPIC_SMALL_FAST_MODEL": "claude-haiku-4.5",
                   "DISABLE_TELEMETRY": "1", "DISABLE_AUTOUPDATER": "1", "DISABLE_ERROR_REPORTING": "1", **extra}
            t = time.time()
            try:
                p = subprocess.run([BIN, "-p", *(["--continue"] if cont else []), prompt], cwd=os.path.join(d, "proj"),
                                   env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=400)
                rc, so = p.returncode, (p.stdout + p.stderr)
            except subprocess.TimeoutExpired:
                rc, so = "timeout", ""
            out.append({"prompt": prompt, "exit": rc, "secs": round(time.time() - t, 1), "stdout": so[-500:]})
    finally:
        srv.terminate(); srv.wait()
        if shim:
            shim.terminate(); shim.wait()
    reqs = [json.loads(l) for l in open(log) if l.strip()]
    return {"case": name, "mode": mode, "cap": cap, "model": model, "env": extra, "turns": out, "requests": reqs}


res = [run(c) for c in CASES if not ONLY or any(c[0].startswith(o) for o in ONLY)]
path = os.path.join(HERE, "results.json" if not ONLY else "results_partial.json")
json.dump(res, open(path, "w"), indent=1)
for r in res:
    msgs = [q for q in r["requests"] if q.get("path") == "/v1/messages"]
    print(f"{r['case']:28} reqs={len(msgs):3} 400s={sum(q['status']==400 for q in msgs):3} "
          f"max_tokens={sorted({q['max_tokens'] for q in msgs}, key=str)} "
          f"turns={[(t['exit'], t['secs'], t['stdout'][-90:].strip()) for t in r['turns']]}")
