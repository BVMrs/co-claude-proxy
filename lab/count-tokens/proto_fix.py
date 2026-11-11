"""Prototype shim-side fix for image over-counting in copilot-api's count_tokens.

Before forwarding a count_tokens body, replace each base64 image block with a short text
placeholder and add Anthropic's documented image cost (width*height/750, at most ~1600 for
images Claude Code has already resized) to the answer. Compares copilot-api's answer for the
original and the rewritten body, for the MCP image cases of run.py, against Claude Code's
truncation threshold (25000).

Usage: python3 proto_fix.py <count_server port> <corpus_markdown.txt>
"""
import base64
import copy
import json
import pathlib
import struct
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
URL = f"http://127.0.0.1:{sys.argv[1]}" + "/v1/messages/count_tokens"
CORPUS = open(sys.argv[2], encoding="utf-8").read()
LIMIT = 25000


def png(px, seed):  # same generator as mcp_server.py
    import random
    import zlib
    r = random.Random(seed)
    raw = b"".join(b"\x00" + r.randbytes(3 * px) for _ in range(px))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", px, px, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def count(body):
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req))["input_tokens"]


def image_tokens(block):
    """Anthropic's estimate: tokens = width*height/750 (PNG/JPEG size from the header)."""
    raw = base64.b64decode(block["source"]["data"][:64] + "==")
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", raw[16:24])
        return min(1600, max(1, w * h // 750))
    return 1600


def fix(body):
    """Return (body without image payloads, tokens to add)."""
    out, extra = copy.deepcopy(body), 0
    for m in out.get("messages", []):
        if isinstance(m.get("content"), list):
            new = []
            for b in m["content"]:
                if b.get("type") == "image" and b.get("source", {}).get("type") == "base64":
                    extra += image_tokens(b)
                    new.append({"type": "text", "text": "[image]"})
                else:
                    new.append(b)
            m["content"] = new
    return out, extra


rows = []
for chars, images, px in ((40_000, 1, 300), (55_000, 1, 300), (20_000, 8, 200), (2_000, 9, 200),
                          (55_000, 1, 120), (40_000, 3, 200), (90_000, 0, 0)):
    content = [{"type": "text", "text": CORPUS[:chars]}] + [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                      "data": base64.b64encode(png(px, k)).decode()}} for k in range(images)]
    body = {"model": "claude-opus-5.5", "messages": [{"role": "user", "content": content}]}
    before = count(body)
    fb, extra = fix(body)
    after = count(fb) + extra
    text_only = count({"model": "claude-opus-5.5", "messages": [{"role": "user", "content": CORPUS[:chars]}]})
    rows.append({"case": f"{chars}c+{images}x{px}px", "copilot_api": before, "fixed": after,
                 "text_only": text_only, "banner_before": before > LIMIT, "banner_after": after > LIMIT})
    print(rows[-1])
json.dump(rows, open(pathlib.Path(__file__).with_name("proto_fix.json"), "w"), indent=1)
