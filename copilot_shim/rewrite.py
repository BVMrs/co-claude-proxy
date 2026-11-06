"""Pure request rewriting: drop or convert a trailing assistant message (prefill).

Claude 4.6+ models served through api.githubcopilot.com answer a request whose
last message has role "assistant" with
``400: This model does not support assistant message prefill``.
This module turns such a request into one that ends in a user turn.

Two modes:

* ``strip`` (default): remove the trailing assistant message(s).
* ``hint``: remove them, and append their text to the last user message as an
  instruction to start the reply with that text, so the intent of the prefill
  is roughly kept.

Works on both the Anthropic Messages shape (``/v1/messages``) and the OpenAI
Chat Completions shape (``/v1/chat/completions``); both use a ``messages`` list
with ``role`` and ``content`` (string or list of blocks).

Anthropic shape only: ``role: "system"`` entries inside ``messages`` are moved
into the top-level ``system`` field first. Claude Code 2.1.x sends its
environment block as a trailing ``role: "system"`` message, and copilot-api
translates every non-user message to an OpenAI ``assistant`` turn
(ericc-ch/copilot-api#272). Left alone, that turns even a plain "hello" into a
request that ends on an assistant turn, and Copilot answers with the prefill
400.
"""

from __future__ import annotations

import copy
import fnmatch
from dataclasses import dataclass, field

MODES = ("strip", "hint", "off")

# Models confirmed (docs/HANDOVER.md) to accept a trailing assistant message.
DEFAULT_PREFILL_OK = ("claude-haiku-4.5",)

HINT_TEMPLATE = (
    "[Begin your reply with exactly the following text, then continue it]\n{prefill}"
)


@dataclass
class Policy:
    mode: str = "strip"
    # Glob patterns of model IDs that accept prefill; requests to them pass unchanged.
    prefill_ok: tuple[str, ...] = DEFAULT_PREFILL_OK
    # Exact model ID rewrites applied before anything else (e.g. dated IDs -> Copilot IDs).
    model_map: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")

    def accepts_prefill(self, model: str | None) -> bool:
        if not model:
            return False
        return any(fnmatch.fnmatchcase(model, pat) for pat in self.prefill_ok)


@dataclass
class Result:
    body: dict
    model: str | None
    last_role: str | None  # role of the last message as received
    original_model: str | None = None
    action: str = "none"  # none | stripped | hinted
    removed: int = 0
    system_folded: int = 0  # role:"system" messages moved into the top-level system field


def _text_of(content) -> str:
    """Concatenate the text parts of a message content (string or block list)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


def _append_text(message: dict, text: str) -> None:
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = [
            {"type": "text", "text": content},
            {"type": "text", "text": text},
        ] if content else [{"type": "text", "text": text}]
    elif isinstance(content, list):
        content.append({"type": "text", "text": text})
    else:
        message["content"] = [{"type": "text", "text": text}]


def _system_blocks(content) -> list[dict]:
    """Text blocks of a system message's content, keeping cache_control."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks = []
    for block in content or ():
        if isinstance(block, dict) and block.get("type") == "text":
            blocks.append(block)
        elif isinstance(block, str) and block:
            blocks.append({"type": "text", "text": block})
    return blocks


def _fold_system_messages(body: dict) -> int:
    """Move role:"system" entries of ``messages`` into the top-level ``system`` field.

    Mutates ``body`` (callers pass a copy). Returns how many messages were moved.
    """
    messages = body["messages"]
    moved = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
    if not moved:
        return 0
    messages[:] = [m for m in messages if not (isinstance(m, dict) and m.get("role") == "system")]

    system = body.get("system")
    if isinstance(system, str):
        blocks = [{"type": "text", "text": system}] if system else []
    elif isinstance(system, list):
        blocks = list(system)
    else:
        blocks = []
    for m in moved:
        blocks.extend(_system_blocks(m.get("content")))
    if blocks:
        body["system"] = blocks
    return len(moved)


def rewrite(body: dict, policy: Policy, anthropic: bool = False) -> Result:
    """Return a rewritten copy of ``body``. Never mutates the input.

    ``anthropic`` marks the Anthropic Messages shape, where ``role: "system"``
    is not a valid message role and is folded into the ``system`` field.
    """
    if not isinstance(body, dict):
        return Result(body=body, model=None, last_role=None)

    out = copy.deepcopy(body)
    original_model = out.get("model")
    model = original_model
    if isinstance(model, str) and model in policy.model_map:
        model = policy.model_map[model]
        out["model"] = model

    messages = out.get("messages")
    if not isinstance(messages, list) or not messages:
        return Result(out, model, None, original_model)

    last_role = messages[-1].get("role") if isinstance(messages[-1], dict) else None
    if policy.mode == "off":
        return Result(out, model, last_role, original_model)

    # Done for every model, including ones that accept prefill: otherwise the
    # system text reaches the model as if the assistant had said it.
    folded = _fold_system_messages(out) if anthropic else 0
    if not messages:
        return Result(out, model, last_role, original_model, system_folded=folded)

    effective_role = messages[-1].get("role") if isinstance(messages[-1], dict) else None
    if effective_role != "assistant" or policy.accepts_prefill(model):
        return Result(out, model, last_role, original_model, system_folded=folded)

    # Collect the run of trailing assistant messages (normally one).
    cut = len(messages)
    while cut > 0 and isinstance(messages[cut - 1], dict) and messages[cut - 1].get("role") == "assistant":
        cut -= 1
    if cut == 0:
        # Nothing but assistant messages: removing them would leave an empty
        # conversation, which is a worse error. Pass through unchanged.
        return Result(out, model, last_role, original_model, system_folded=folded)

    removed = messages[cut:]
    del messages[cut:]
    action = "stripped"

    if policy.mode == "hint":
        prefill = "".join(_text_of(m.get("content")) for m in removed).strip()
        if prefill:
            target = messages[-1]
            # Only append to a plain user turn; never touch tool/system turns
            # (the OpenAI shape uses role "tool" for tool results).
            if target.get("role") == "user":
                _append_text(target, HINT_TEMPLATE.format(prefill=prefill))
            else:
                messages.append({"role": "user", "content": HINT_TEMPLATE.format(prefill=prefill)})
            action = "hinted"

    return Result(out, model, last_role, original_model, action, len(removed), folded)
