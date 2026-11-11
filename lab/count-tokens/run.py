"""Which Claude Code 2.1.321 size decisions flip when count_tokens is answered by copilot-api 0.7.0?

Runs real Claude Code (headless) against fake_upstream.py. Its count_tokens route is answered
by one of three backends:
  copilot   copilot-api's own handler (count_server.ts), model found in its list
  miss      the same handler, but the requested model ID is not in copilot-api's model list
  none      404 (no count route: Claude Code falls back to its local estimate)

Cases:
  read   the main loop Reads one file (code / json / markdown) of a given size
  mcp    an MCP tool returns N characters of text plus K noise PNG screenshots

Records, per case: the count requests (model, body size, answer) and the tool_result outcome
(accepted / rejected as too large / truncated).

Usage (repo root; needs bun, a copilot-api checkout with `bun install` done, and claude 2.1.321):
    python3 lab/count-tokens/run.py <copilot-api dir> <claude bin> [out.json]
No Copilot login, no network.
"""
import concurrent.futures as cf
import glob
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
API, BIN = sys.argv[1], sys.argv[2]
OUT = sys.argv[3] if len(sys.argv) > 3 else str(HERE / "results.json")
WORK = pathlib.Path(os.environ.get("WORK", HERE.parents[1] / ".agent-work" / "count-tokens"))
MODEL = os.environ.get("MODEL", "claude-opus-5.5")


def corpora():
    WORK.mkdir(parents=True, exist_ok=True)
    srcs = {
        "code": [f"{API}/node_modules/typescript/lib/lib.dom.d.ts"],
        "json": [f"{API}/bun.lock"],
        "markdown": sorted(glob.glob(f"{API}/node_modules/**/README.md", recursive=True))[:400],
    }
    out = {}
    for kind, files in srcs.items():
        text = "".join(open(f, encoding="utf-8", errors="replace").read() for f in files)
        while len(text) < 400_000:
            text += text
        p = WORK / f"corpus_{kind}.txt"
        p.write_text(text[:400_000], encoding="utf-8")
        out[kind] = p
    return out


def start_count_servers():
    procs = {}
    for name, port, ids in (("copilot", 4301, os.environ.get("COPILOT_IDS", "claude-opus-5.5,claude-sonnet-5.5,claude-haiku-4.5")), ("miss", 4302, "some-other-model")):
        procs[name] = (subprocess.Popen(["bun", str(HERE / "count_server.ts"), API, str(port), ids],
                                        stderr=subprocess.DEVNULL), f"http://127.0.0.1:{port}")
    time.sleep(2)
    return procs


def run_case(case, backends, port):
    w = WORK / "runs" / case["id"]
    shutil.rmtree(w, ignore_errors=True)
    (w / "home").mkdir(parents=True)
    (w / "proj").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=w / "proj")
    (w / "home" / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True, "numStartups": 5,
        "projects": {str(w / "proj"): {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True}}}))
    (w / "mcp.json").write_text(json.dumps({"mcpServers": {"lab": {
        "type": "stdio", "command": "python3", "args": ["-I", str(HERE / "mcp_server.py"), str(case["corpus"])]}}}))
    if case["kind"] == "read":
        text = case["corpus"].read_text(encoding="utf-8")[: case["chars"]]
        (w / "proj" / "big.txt").write_text(text, encoding="utf-8")
        step = 'Read {"file_path": "%s"}' % (w / "proj" / "big.txt")
    else:
        step = 'mcp__lab__dump {"chars": %d, "images": %d, "px": %d}' % (case["chars"], case["images"], case["px"])
    log = w / "requests.jsonl"
    count = backends[case["backend"]][1] if case["backend"] in backends else "404"
    srv = subprocess.Popen([sys.executable, str(HERE / "fake_upstream.py"), str(port), str(log)],
                           env={**os.environ, "COUNT": count})
    time.sleep(0.4)
    env = {"PATH": os.environ["PATH"], "HOME": str(w / "home"), "TERM": "dumb",
           "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_AUTH_TOKEN": "dummy",
           "ANTHROPIC_MODEL": MODEL, "DISABLE_TELEMETRY": "1", "DISABLE_AUTOUPDATER": "1",
           "DISABLE_ERROR_REPORTING": "1"}
    try:
        subprocess.run(["timeout", "150", BIN, "-p", "PLAN: " + step, "--mcp-config", str(w / "mcp.json"),
                        "--strict-mcp-config", "--allowedTools", "Read mcp__lab__dump",
                        "--output-format", "json"], cwd=w / "proj", env=env, stdout=open(w / "result.json", "w"),
                       stderr=open(w / "stderr.log", "w"))
    finally:
        srv.terminate()
    return analyse(case, log)


def analyse(case, log):
    counts, outcome, result_chars, images = [], "no tool_result", None, None
    for line in (open(log) if log.exists() else []):
        rec = json.loads(line)
        if "count" in rec:
            counts.append(rec["count"])
            continue
        body = rec.get("body") or {}
        for m in body.get("messages") or []:
            if m.get("role") != "user" or not isinstance(m.get("content"), list):
                continue
            for b in m["content"]:
                if b.get("type") != "tool_result":
                    continue
                c = b.get("content")
                blocks = [{"type": "text", "text": c}] if isinstance(c, str) else (c or [])
                text = "".join(x.get("text", "") for x in blocks if x.get("type") == "text")
                images = sum(1 for x in blocks if x.get("type") == "image")
                result_chars = len(text)
                if "exceeds maximum allowed tokens" in text:
                    outcome = "rejected"
                elif "OUTPUT TRUNCATED" in text or "left out" in text:
                    outcome = "truncated"
                elif b.get("is_error"):
                    outcome = "error: " + text[:120]
                else:
                    outcome = "accepted"
    return {**{k: (str(v) if isinstance(v, pathlib.Path) else v) for k, v in case.items()},
            "counts": [{k: c[k] for k in ("model", "bytes", "status", "answer")} for c in counts],
            "outcome": outcome, "result_chars": result_chars, "result_images": images}


def cases(corp):
    out = []
    for kind in ("code", "json", "markdown"):
        for chars in (20_000, 30_000, 50_000, 70_000, 90_000, 110_000, 150_000, 250_000):
            for be in ("copilot", "miss", "none"):
                out.append({"id": f"read-{kind}-{chars}-{be}", "kind": "read", "corpus": corp[kind],
                            "chars": chars, "backend": be})
    out.append({"id": "model-probe", "kind": "read", "corpus": corp["json"], "chars": 70_000, "backend": "copilot"})
    for chars, images, px in ((60_000, 0, 0), (90_000, 0, 0), (40_000, 1, 300), (55_000, 1, 300),
                              (20_000, 8, 200), (2_000, 9, 200), (55_000, 1, 120), (40_000, 3, 200)):
        for be in ("copilot", "miss", "none"):
            out.append({"id": f"mcp-{chars}-{images}x{px}-{be}", "kind": "mcp", "corpus": corp["markdown"],
                        "chars": chars, "images": images, "px": px, "backend": be})
    return out


def main():
    corp = corpora()
    backends = start_count_servers()
    todo = cases(corp)
    only = os.environ.get("ONLY")
    if only:
        todo = [c for c in todo if only in c["id"]]
    try:
        with cf.ThreadPoolExecutor(int(os.environ.get("JOBS", "6"))) as ex:
            res = list(ex.map(lambda ic: run_case(ic[1], backends, 4400 + ic[0]), enumerate(todo)))
    finally:
        for p, _ in backends.values():
            p.terminate()
    json.dump(res, open(OUT, "w"), indent=1)
    for r in res:
        ans = [c["answer"] for c in r["counts"]]
        print(f'{r["id"]:38s} {r["outcome"]:10s} chars={r["result_chars"]} imgs={r["result_images"]} counts={ans}')


main()
