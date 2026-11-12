"""Prototype replacement for the PR's copilot_shim/errors.py (lab only, not a patch to apply blindly).

Differences from PR head 643b550:
  * only an error *message* (a "message" field, at any depth, incl. JSON-in-string) is matched,
    and Copilot's phrase must START that message; a phrase quoted inside some other error
    (user text, tool description) no longer triggers compaction;
  * the error *code* is matched only in a "code" field;
  * OpenAI's context_length_exceeded wording/code is recognised too (numbers parsed);
  * thousands separators in the numbers are accepted;
  * any decode problem (incl. RecursionError on deeply nested bodies) passes the body through.
"""
from __future__ import annotations

import json
import re

CODES = {"model_max_prompt_tokens_exceeded", "context_length_exceeded"}
N = r"(\d[\d,]*)"
COPILOT_RE = re.compile(rf"\s*prompt token count (?:of )?{N} exceeds the limit (?:of )?{N}", re.I)
COPILOT_PHRASE_RE = re.compile(r"\s*prompt token count\b.*?\bexceeds the limit", re.I | re.S)
OPENAI_RE = re.compile(rf"\s*this model's maximum context length is {N} tokens\. however, "
                       rf"(?:your messages resulted in|you requested) {N} tokens", re.I)


def _errors(obj, depth=0):
    """Yield (message, code) for every dict carrying a "message" string, descending into JSON strings."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        msg, code = obj.get("message"), obj.get("code")
        if isinstance(msg, str):
            yield msg, code if isinstance(code, str) else None
        for v in obj.values():
            yield from _errors(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _errors(v, depth + 1)
    elif isinstance(obj, str) and obj.lstrip()[:1] in ("{", "["):
        try:
            yield from _errors(json.loads(obj), depth + 1)
        except (ValueError, RecursionError):
            return


def _num(s):
    return int(s.replace(",", ""))


def rewrite_prompt_too_long(status: int, data: bytes) -> bytes | None:
    if status != 400 or not data:
        return None
    try:
        errors = list(_errors(json.loads(data)))
    except (ValueError, RecursionError):
        return None
    matched, numbers, text = False, None, None
    for msg, code in errors:
        m = COPILOT_RE.match(msg)
        o = OPENAI_RE.match(msg)
        if m:
            numbers = numbers or (_num(m.group(1)), _num(m.group(2)))
        elif o:
            numbers = numbers or (_num(o.group(2)), _num(o.group(1)))
        if m or o or COPILOT_PHRASE_RE.match(msg) or code in CODES:
            matched = True
            if not msg.lstrip().startswith(("{", "[")):
                text = text or msg
    if not matched:
        return None
    message = (f"prompt is too long: {numbers[0]} tokens > {numbers[1]} maximum" if numbers
               else f"prompt is too long: {text or 'model_max_prompt_tokens_exceeded'}")
    return json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": message}}).encode()
