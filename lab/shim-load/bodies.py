"""Synthetic Claude Code /v1/messages bodies of a target size.

Shape follows lab/cache-prefix/capture_shape.json (Claude Code 2.1.321): 3 system blocks
(~6 KB), ~30 KB of tool definitions, a user turn of 3 text blocks, a role:"system"
environment message (~9 KB, so the shim always folds and re-serializes), then
assistant tool_use / user tool_result pairs whose results are code-like text, ending on a
user turn. Sizes are grown with more tool rounds until the JSON reaches the target.
No real prompt text; content is generated.
"""
import json
import random

CODE = (
    "def handler(request, ctx):\n    data = request.json()\n    if not data:\n"
    "        return {'error': 'empty'}, 400\n    for k, v in sorted(data.items()):\n"
    "        ctx.log.info('%s=%r', k, v)\n    return {'ok': True}, 200\n\n"
)
CJK = "这是一个用于测试的中文注释，包含非ASCII字符。日本語のコメントも含まれます。"


def _tools():
    return [{
        "name": f"Tool{i}",
        "description": "Performs an operation. " * 40,
        "input_schema": {"type": "object", "properties": {
            f"p{j}": {"type": "string", "description": "parameter " * 8} for j in range(6)},
            "required": ["p0"]},
    } for i in range(24)]


def make(target_bytes, *, seed=0, cjk=False, image_kb=0, trailing_assistant=False):
    rnd = random.Random(seed)
    body = {
        "model": "claude-opus-5.5", "max_tokens": 32000, "stream": True,
        "system": [{"type": "text", "text": "x" * 74}, {"type": "text", "text": "y" * 62},
                   {"type": "text", "text": ("You are an agent. " * 330)[:5944],
                    "cache_control": {"type": "ephemeral"}}],
        "tools": _tools(),
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "a" * 567},
                                         {"type": "text", "text": "b" * 571},
                                         {"type": "text", "text": "fix the bug in handler.py"}]},
            {"role": "system", "content": [{"type": "text", "text": ("# Environment\n" + "env line\n" * 1100)[:9183],
                                            "cache_control": {"type": "ephemeral"}}]},
        ],
    }
    msgs = body["messages"]
    if image_kb:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        msgs[0]["content"].append({"type": "image", "source": {
            "type": "base64", "media_type": "image/png",
            "data": "".join(rnd.choice(alphabet) for _ in range(image_kb * 1024))}})
    filler = (CJK * 4 + "\n" + CODE) if cjk else CODE
    size = len(encode(body))
    n = 0
    while size < target_bytes:
        n += 1
        tid = f"toolu_{n:024d}"
        result = filler * rnd.randint(10, 40)
        a = {"role": "assistant", "content": [
            {"type": "text", "text": "Let me look at the next file."},
            {"type": "tool_use", "id": tid, "name": "Read", "input": {"file_path": f"src/mod{n}.py"}}]}
        u = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": result}]}
        msgs += [a, u]
        size += len(encode(a)) + len(encode(u)) + 2
    msgs.append({"role": "user", "content": [{"type": "text", "text": "continue"}]})
    if trailing_assistant:
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": "{"}]})
    return body


def encode(body):
    # Like Node's JSON.stringify: compact, UTF-8, no \\u escapes for non-ASCII.
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
