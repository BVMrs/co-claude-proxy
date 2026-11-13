"""Prototype shim pass for attachments in tool results (stdlib only, not wired into the shim).

copilot-api 0.7.0 maps each Anthropic tool_result to an OpenAI role:"tool" message and copies
image blocks into it as image_url parts; OpenAI-style Chat Completions reject images outside
role:"user". It also drops every `document` block (PDF) without a trace.

hoist_attachments(body):
  * image blocks inside a tool_result move to the same user message, after the tool_results
    (copilot-api then emits: tool messages ..., one user message with the images), and the
    tool_result keeps a text marker "[image N attached in the next user message]";
  * document blocks (anywhere in a user message) become a text note that the PDF could not be
    forwarded, so the model knows instead of guessing.
Returns (new_body, stats).
"""
from __future__ import annotations

import copy


def _doc_note(b):
    s = b.get("source") or {}
    size = len(s.get("data") or "") * 3 // 4
    return {"type": "text", "text": (
        f"[A {s.get('media_type', 'document')} attachment (~{size} bytes) was dropped: the Copilot "
        "chat endpoint cannot receive documents. Extract its text with a tool (e.g. `pdftotext file.pdf -`) "
        "if you need the content.]")}


def hoist_attachments(body):
    stats = {"images_hoisted": 0, "documents_replaced": 0}
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return body, stats
    out = copy.deepcopy(body)
    for m in out["messages"]:
        if not isinstance(m, dict) or m.get("role") != "user" or not isinstance(m.get("content"), list):
            continue
        hoisted, new = [], []
        for b in m["content"]:
            if not isinstance(b, dict):
                new.append(b)
                continue
            if b.get("type") == "document":
                new.append(_doc_note(b))
                stats["documents_replaced"] += 1
                continue
            if b.get("type") == "tool_result" and isinstance(b.get("content"), list):
                inner = []
                for x in b["content"]:
                    if isinstance(x, dict) and x.get("type") == "image":
                        hoisted.append(x)
                        inner.append({"type": "text", "text": f"[image {len(hoisted)} attached in the next user message]"})
                        stats["images_hoisted"] += 1
                    elif isinstance(x, dict) and x.get("type") == "document":
                        inner.append(_doc_note(x))
                        stats["documents_replaced"] += 1
                    else:
                        inner.append(x)
                b = {**b, "content": inner}
            new.append(b)
        if hoisted:
            new.append({"type": "text", "text": f"[{len(hoisted)} image(s) returned by the tool call(s) above:]"})
            new.extend(hoisted)
        m["content"] = new
    return out, stats
