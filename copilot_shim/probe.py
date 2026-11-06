"""List the model IDs the proxy offers and probe which ones accept assistant prefill.

Usage::

    python3 -m copilot_shim.probe                       # probe claude-* models on localhost:4141
    python3 -m copilot_shim.probe --list                # only list model IDs
    python3 -m copilot_shim.probe --model claude-opus-5.5 --model claude-haiku-4.5
    python3 -m copilot_shim.probe --json > probe.json

For each model it sends two tiny requests to ``/v1/messages``:

* ``baseline``: ends in a user turn. If this fails, the model ID itself is not
  usable (for example ``model_not_supported``), whatever happens with prefill.
* ``prefill``: ends in an assistant turn.

Every request uses ``max_tokens`` 8, so a full run costs a few premium requests
per model. Take model IDs for Claude Code only from the ``--list`` output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

PREFILL_MARKER = "prefill"


@dataclass
class Probe:
    model: str
    baseline_status: int | None
    prefill_status: int | None
    verdict: str  # accepts-prefill | rejects-prefill | model-unusable | error
    detail: str = ""


def _request(method: str, url: str, token: str, payload: dict | None = None, timeout: float = 60.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("x-api-key", token)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return None, str(exc)


def list_models(base_url: str, token: str) -> list[str]:
    status, text = _request("GET", f"{base_url.rstrip('/')}/v1/models", token)
    if status != 200:
        raise RuntimeError(f"GET /v1/models failed ({status}): {text[:300]}")
    data = json.loads(text)
    items = data.get("data", data) if isinstance(data, dict) else data
    ids = [m["id"] if isinstance(m, dict) else str(m) for m in items]
    return sorted(dict.fromkeys(ids))


def _error_message(text: str) -> str:
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()[:300]
    err = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(err, dict):
        msg = err.get("message") or ""
        code = err.get("code") or err.get("type") or ""
        return f"{code}: {msg}".strip(": ")[:300]
    return str(err)[:300]


def probe_model(base_url: str, token: str, model: str) -> Probe:
    url = f"{base_url.rstrip('/')}/v1/messages"
    user = {"role": "user", "content": "Reply with one word: ok"}
    base = {"model": model, "max_tokens": 8, "messages": [user]}
    pre = {"model": model, "max_tokens": 8,
           "messages": [user, {"role": "assistant", "content": "The word is"}]}

    b_status, b_text = _request("POST", url, token, base)
    if b_status is None:
        return Probe(model, None, None, "error", b_text)
    if b_status != 200:
        verdict = "model-unusable" if 400 <= b_status < 500 else "error"
        return Probe(model, b_status, None, verdict, _error_message(b_text))

    p_status, p_text = _request("POST", url, token, pre)
    if p_status == 200:
        return Probe(model, b_status, p_status, "accepts-prefill")
    if p_status is None:
        return Probe(model, b_status, None, "error", p_text)
    msg = _error_message(p_text)
    if p_status == 400 and PREFILL_MARKER in msg.lower():
        return Probe(model, b_status, p_status, "rejects-prefill", msg)
    return Probe(model, b_status, p_status, "error", msg)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m copilot_shim.probe",
                                description="List model IDs and probe prefill support per model.")
    p.add_argument("--base-url", default=os.environ.get("PROBE_BASE_URL", "http://localhost:4141"),
                   help="copilot-api base URL (default http://localhost:4141; probe the proxy, not the shim)")
    p.add_argument("--token", default=os.environ.get("ANTHROPIC_AUTH_TOKEN", "dummy"),
                   help="sent as x-api-key/Bearer (default $ANTHROPIC_AUTH_TOKEN or 'dummy')")
    p.add_argument("--list", action="store_true", help="only list model IDs")
    p.add_argument("--model", action="append", help="probe this model (repeatable; default: all claude* IDs)")
    p.add_argument("--all", action="store_true", help="probe every listed model, not only claude*")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)

    try:
        ids = list_models(args.base_url, args.token)
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list:
        print(json.dumps(ids, indent=2) if args.json else "\n".join(ids))
        return 0

    if args.model:
        targets = args.model
        unknown = [m for m in targets if m not in ids]
        for m in unknown:
            print(f"warning: {m!r} is not in /v1/models; expect model_not_supported", file=sys.stderr)
    else:
        targets = ids if args.all else [m for m in ids if "claude" in m.lower()]

    results = []
    for m in targets:
        r = probe_model(args.base_url, args.token, m)
        results.append(r)
        if not args.json:
            print(f"{r.model:32} baseline={r.baseline_status!s:4} prefill={r.prefill_status!s:4} "
                  f"{r.verdict}{'  ' + r.detail if r.detail else ''}", flush=True)

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        ok = [r.model for r in results if r.verdict == "accepts-prefill"]
        if ok:
            print("\nshim flags for models that accept prefill: "
                  + " ".join(f"--prefill-ok {m}" for m in ok))
    return 1 if any(r.verdict == "error" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
