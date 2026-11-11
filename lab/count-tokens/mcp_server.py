"""Minimal stdio MCP server (no deps) with one tool, `dump`, for output-size tests.

Usage: python3 mcp_server.py <corpus.txt>
dump {"chars": N, "images": K, "px": P} returns the first N characters of the corpus as one
text block plus K PNG images of P x P pixels (deterministic noise, so the PNG barely
compresses: about 3*P*P bytes, like a busy screenshot).
"""
import base64
import json
import random
import struct
import sys
import zlib

CORPUS = open(sys.argv[1], encoding="utf-8").read()


def png(px, seed):
    r = random.Random(seed)
    raw = b"".join(b"\x00" + r.randbytes(3 * px) for _ in range(px))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", px, px, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


TOOL = {"name": "dump", "description": "Returns a text dump and optional screenshots.",
        "inputSchema": {"type": "object", "properties": {
            "chars": {"type": "integer"}, "images": {"type": "integer"}, "px": {"type": "integer"}},
            "required": ["chars"]}}


def reply(i, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": i, "result": result}) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        m = json.loads(line)
    except ValueError:
        continue
    meth, i = m.get("method"), m.get("id")
    if i is None:
        continue
    if meth == "initialize":
        reply(i, {"protocolVersion": m["params"].get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "lab", "version": "0"}})
    elif meth == "tools/list":
        reply(i, {"tools": [TOOL]})
    elif meth == "tools/call":
        a = m["params"].get("arguments") or {}
        content = [{"type": "text", "text": CORPUS[: int(a.get("chars", 0))]}]
        for k in range(int(a.get("images", 0))):
            content.append({"type": "image", "mimeType": "image/png",
                            "data": base64.b64encode(png(int(a.get("px", 200)), k)).decode()})
        reply(i, {"content": content})
    else:
        reply(i, {})
