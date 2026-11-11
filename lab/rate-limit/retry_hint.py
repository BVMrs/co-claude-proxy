"""Prototype for the shim: turn a copilot-api 429 into one Claude Code handles well.

copilot-api 0.7.0 (src/lib/error.ts forwardError) re-wraps Copilot's error body as
{"error": {"message": "<Copilot body as text>", "type": "error"}} in a fresh response, so any
retry-after header from Copilot is lost. This recovers a wait from the message text and returns
(new_body, extra_headers) for the shim to send instead.

Claude Code 2.1.321 behaviour measured in lab/2026-11-10-rate-limit.md:
  retry-after <= 60 s  -> waits exactly that long, then retries (up to 10 retries)
  retry-after >= 61 s  -> fails at once and shows the message
  x-should-retry:false -> fails at once
  no header            -> 10 retries over ~3 minutes (0.5 s doubling, capped ~32 s)

Run: python3 retry_hint.py   (self-check, no network)
"""
import json
import re

_UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400}
_WAIT = re.compile(r"(\d+(?:\.\d+)?)\s*(day|hour|hr|minute|min|second|sec)s?\b", re.I)
# Codes that name a quota window rather than a momentary burst limit.
_HARD_CODES = ("user_global_rate_limited", "user_weekly_rate_limited", "quota_exceeded")


def _inner(body):
    """Return (message, code) from a copilot-api error body, tolerating every nesting we have seen."""
    try:
        outer = json.loads(body)
    except ValueError:
        return body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body), None
    msg = (outer.get("error") or {}).get("message") if isinstance(outer, dict) else None
    if not isinstance(msg, str):
        return json.dumps(outer), None
    try:
        inner = json.loads(msg)
    except ValueError:
        return msg, None
    if isinstance(inner, dict):
        err = inner.get("error") if isinstance(inner.get("error"), dict) else inner
        return str(err.get("message", msg)), err.get("code")
    return msg, None


def wait_seconds(message):
    """Sum every '<n> <unit>' in the text after 'wait'/'try again in'; None if there is none."""
    m = re.search(r"(?:wait|try again in|retry in|resets? in)(.*)", message, re.I | re.S)
    if not m:
        return None
    parts = _WAIT.findall(m.group(1))
    if not parts:
        return None
    return int(sum(float(n) * _UNITS[u.lower()] for n, u in parts))


def rewrite_429(body):
    """Return (anthropic_error_body_bytes, headers_dict) for a copilot-api 429 body."""
    message, code = _inner(body)
    headers = {}
    secs = wait_seconds(message)
    if secs is not None:
        headers["retry-after"] = str(secs)       # >60 s: Claude Code stops at once and shows it
    elif code and any(code.startswith(c) for c in _HARD_CODES):
        headers["x-should-retry"] = "false"      # quota window, length unknown: don't hammer
    out = {"type": "error", "error": {"type": "rate_limit_error", "message": message}}
    return json.dumps(out).encode(), headers


if __name__ == "__main__":
    def cp(msg, code=None):
        inner = {"error": {"message": msg, **({"code": code} if code else {})}}
        return json.dumps({"error": {"message": json.dumps(inner), "type": "error"}}).encode()

    cases = [
        (cp("Sorry, you've exceeded your 5 hour session limits. Please wait 5 hours 52 minutes "
            "before trying again.", "user_global_rate_limited:pro_plus"), {"retry-after": "21120"}),
        (cp("Rate limited. Please try again in 30 seconds.", "rate_limited"), {"retry-after": "30"}),
        (cp("You have exceeded your weekly limit.", "user_weekly_rate_limited:pro"),
         {"x-should-retry": "false"}),
        (cp("Too many requests", "rate_limited"), {}),
        # copilot-api's own --rate-limit rejection (src/lib/rate-limit.ts)
        (json.dumps({"error": {"message": json.dumps({"message": "Rate limit exceeded"}),
                               "type": "error"}}).encode(), {}),
        (b"not json", {}),
    ]
    ok = 0
    for body, want in cases:
        out, hdrs = rewrite_429(body)
        parsed = json.loads(out)
        good = hdrs == want and parsed["error"]["type"] == "rate_limit_error"
        ok += good
        print("ok  " if good else "FAIL", hdrs, "|", parsed["error"]["message"][:70])
    print(f"{ok}/{len(cases)} cases")
