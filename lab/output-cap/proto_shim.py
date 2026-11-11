"""Prototype: the real copilot_shim with one extra step, clamping max_tokens per model.

Wraps copilot_shim.server.rewrite so that after the normal rewrite, an Anthropic or OpenAI
body whose max_tokens exceeds the cap for its model (glob table below, or CAPS env as
"glob=N,glob=N") is lowered to the cap. Everything else is the unchanged shim.

Usage: CAPS='claude-*=32000' python3 -m ... proto_shim.py <listen-port> <upstream-url>
(run with the repo root on PYTHONPATH)
"""
import fnmatch
import logging
import os
import sys

import copilot_shim.server as server
from copilot_shim.rewrite import Policy, rewrite as base_rewrite

CAPS = [(g, int(n)) for g, _, n in (i.partition("=") for i in os.environ.get("CAPS", "claude-*=32000").split(","))]


def cap_for(model):
    for glob, n in CAPS:
        if isinstance(model, str) and fnmatch.fnmatchcase(model, glob):
            return n
    return None


def clamp_rewrite(body, policy, anthropic=False):
    res = base_rewrite(body, policy, anthropic=anthropic)
    cap = cap_for(res.model)
    mt = res.body.get("max_tokens") if isinstance(res.body, dict) else None
    if cap and isinstance(mt, int) and not isinstance(mt, bool) and mt > cap:
        res.body["max_tokens"] = cap
        # thinking.budget_tokens must stay below max_tokens on the Anthropic API
        th = res.body.get("thinking")
        if isinstance(th, dict) and isinstance(th.get("budget_tokens"), int) and th["budget_tokens"] >= cap:
            th["budget_tokens"] = max(1024, cap - 1)
        logging.getLogger("copilot_shim").info("clamped max_tokens %d -> %d for %s", mt, cap, res.model)
        if res.action == "none":
            res.action = "clamped"
    return res


server.rewrite = clamp_rewrite

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    srv = server.serve("127.0.0.1", int(sys.argv[1]), sys.argv[2], Policy())
    srv.serve_forever()
