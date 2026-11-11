"""Rewrite upstream error bodies into shapes Claude Code recognises.

Copilot rejects a prompt over the model's ``max_prompt_tokens`` with a 400 whose
text is ``prompt token count of N exceeds the limit of M`` (code
``model_max_prompt_tokens_exceeded``). copilot-api forwards it with the Copilot
JSON stringified inside ``{"error": {"message": "<json>", "type": "error"}}``.

Claude Code only detects a context overflow by the text ``prompt is too long``
(it parses ``N tokens > M``), and then compacts and retries. Any other text is a
plain 400, the failed turn stays in history and the session breaks for good
(lab note compact-cap, 2026-11-09). So the shim rewrites this one error into
Anthropic's ``invalid_request_error`` form. Everything else passes through.
"""

from __future__ import annotations

import json
import re

PROMPT_LIMIT_CODE = "model_max_prompt_tokens_exceeded"
# Loose on purpose: the exact Copilot wording is not confirmed on a live account.
PROMPT_LIMIT_RE = re.compile(
    r"prompt token count (?:of )?(\d+) exceeds the limit (?:of )?(\d+)", re.IGNORECASE
)
PROMPT_LIMIT_PHRASE_RE = re.compile(r"prompt token count.*exceeds the limit", re.IGNORECASE | re.DOTALL)


def _strings(obj, depth: int = 0):
    """Yield every string in a JSON value, descending into strings that hold JSON."""
    if depth > 8:
        return
    if isinstance(obj, str):
        yield obj
        s = obj.strip()
        if s[:1] in ("{", "["):
            try:
                inner = json.loads(s)
            except ValueError:
                return
            yield from _strings(inner, depth + 1)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, depth + 1)


def _original_message(payload) -> str:
    """The most specific human-readable message in a (possibly nested) error body."""
    best = None
    for s in _strings(payload):
        if PROMPT_LIMIT_PHRASE_RE.search(s) and not s.lstrip().startswith(("{", "[")):
            return s
        if best is None and s and s != PROMPT_LIMIT_CODE and not s.lstrip().startswith(("{", "[")):
            best = s
    return best or PROMPT_LIMIT_CODE


def rewrite_prompt_too_long(status: int, data: bytes) -> bytes | None:
    """Return an Anthropic ``prompt is too long`` error body, or None to pass ``data`` through.

    Only a 400 with a JSON body that names Copilot's prompt-token limit (by the
    phrase or the error code) is rewritten. If N and M can be parsed the message
    is ``prompt is too long: N tokens > M maximum``; otherwise the original text
    is kept after ``prompt is too long: `` so Claude Code still detects it.
    """
    if status != 400 or not data:
        return None
    try:
        payload = json.loads(data)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None

    matched = False
    numbers = None
    for s in _strings(payload):
        if numbers is None:
            m = PROMPT_LIMIT_RE.search(s)
            if m:
                numbers = (int(m.group(1)), int(m.group(2)))
        if s == PROMPT_LIMIT_CODE or PROMPT_LIMIT_PHRASE_RE.search(s):
            matched = True
    if not matched:
        return None

    if numbers is not None:
        message = f"prompt is too long: {numbers[0]} tokens > {numbers[1]} maximum"
    else:
        message = f"prompt is too long: {_original_message(payload)}"
    return json.dumps(
        {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    ).encode()
