r"""Run a corpus of upstream 400 bodies through the PR's rewrite_prompt_too_long and
classify what Claude Code 2.1.321 would make of the result.

CC detection (from the binary's strings): overflow iff the error message, lowercased,
contains "prompt is too long" or "input is too long for requested model"; token
numbers via the regex:  prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)  (case-insensitive).
Usage: python3 corpus.py <checkout-dir>   (a dir containing copilot_shim/errors.py: PR head or a copy with errors_fix.py)
"""
import json
import re
import sys
import time

sys.path.insert(0, sys.argv[1])
from copilot_shim.errors import rewrite_prompt_too_long  # noqa: E402

NUM = re.compile(r"prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)", re.I)


def fwd(inner: dict) -> bytes:  # copilot-api 0.7.0 forwardError: Copilot JSON as a string
    return json.dumps({"error": {"message": json.dumps(inner), "type": "error"}}).encode()


def cc_view(status: int, body: bytes):
    if status == 413:  # CC treats HTTP 413 itself as an overflow (e2e P-413: compacts, 0 failed turns)
        return True, None
    try:
        msg = json.loads(body)["error"]["message"]
    except Exception:
        msg = body.decode("utf-8", "replace")
    low = msg.lower()
    overflow = "prompt is too long" in low or "input is too long for requested model" in low
    m = NUM.search(msg)
    return overflow, (m.groups() if m else None)


CANON = fwd({"error": {"message": "prompt token count of 132456 exceeds the limit of 128000",
                       "code": "model_max_prompt_tokens_exceeded"}})
CASES = [
    # (id, status, should CC see an overflow?, body)
    ("canonical", 400, True, CANON),
    ("commas", 400, True, fwd({"error": {"message": "prompt token count of 132,456 exceeds the limit of 128,000",
                                         "code": "model_max_prompt_tokens_exceeded"}})),
    ("code-only", 400, True, fwd({"error": {"message": "Request too large",
                                            "code": "model_max_prompt_tokens_exceeded"}})),
    ("openai-ctx", 400, True, fwd({"error": {
        "message": "This model's maximum context length is 128000 tokens. However, your messages resulted in "
                   "132456 tokens. Please reduce the length of the messages.",
        "type": "invalid_request_error", "code": "context_length_exceeded"}})),
    ("anthropic-native", 400, True, fwd({"type": "error", "error": {
        "type": "invalid_request_error", "message": "prompt is too long: 210000 tokens > 200000 maximum"}})),
    ("413-canonical", 413, True, CANON),
    ("unrelated-400", 400, False, fwd({"error": {"message": "The requested model is not supported.",
                                                 "code": "model_not_supported"}})),
    ("echo-user-text", 400, False, fwd({"error": {
        "message": "Invalid 'messages[2].content[0].text': contains disallowed sequence: "
                   "'why does copilot say prompt token count of 5 exceeds the limit of 4?'",
        "code": "invalid_request_body"}})),
    ("echo-tool-desc", 400, False, fwd({"error": {
        "message": "Invalid schema for function 'explain_error': description 'Explains errors like "
                   "\"prompt token count exceeds the limit\"' is not allowed",
        "code": "invalid_function_parameters"}})),
    ("max-tokens", 400, False, fwd({"error": {
        "message": "max_tokens is too large: 128000. This model supports at most 32000 completion tokens",
        "code": "invalid_request_body"}})),
]

print(f"{'case':18} {'st':>3} {'shim':9} {'cc_overflow':11} {'cc_numbers':22} verdict")
for cid, status, want, body in CASES:
    out = rewrite_prompt_too_long(status, body)
    ov, nums = cc_view(status, out if out is not None else body)
    verdict = "ok" if ov == want else ("FALSE-NEG (session breaks)" if want else "FALSE-POS (spurious compaction)")
    if ov and want and nums is None:
        verdict = "ok (no numbers)"
    print(f"{cid:18} {status:>3} {'rewritten' if out is not None else 'pass':9} {str(ov):11} {str(nums):22} {verdict}")

print("\n# robustness: exceptions escaping rewrite_prompt_too_long, and time (each probe in a child, 60 s limit)")
import multiprocessing as mp  # noqa: E402

PROBES = []


def _run(status, body, q):
    t = time.perf_counter()
    try:
        out = rewrite_prompt_too_long(status, body)
        res = "rewritten" if out else "pass"
    except BaseException as e:  # noqa: BLE001
        res = f"RAISES {type(e).__name__}: {str(e)[:60]}"
    q.put((1e3 * (time.perf_counter() - t), res))


def probe(name, status, body, limit=60):
    q = mp.Queue()
    p = mp.Process(target=_run, args=(status, body, q))
    p.start()
    p.join(limit)
    if p.is_alive():
        p.kill()
        ms, res = limit * 1e3, f"HANGS (killed after {limit} s)"
    else:
        ms, res = q.get()
    print(f"{name:32} {len(body):>9} B {ms:9.1f} ms  {res}", flush=True)


if __name__ == "__main__":
    probe("deep nesting 100k '['", 400, b'{"error":' + b"[" * 100_000 + b"]" * 100_000 + b"}")
    s = "prompt token count of 5 exceeds the limit of 4"
    for _ in range(50):
        s = json.dumps({"m": s})
    probe("JSON-in-string depth 50", 400, s.encode())
    probe("5000-digit token count", 400,
          fwd({"error": {"message": f"prompt token count of {'9' * 5000} exceeds the limit of 128000"}}))
    for n in (1_000, 2_000, 4_000, 8_000, 16_000):
        probe(f"'prompt token count ' x{n}", 400, json.dumps({"error": {"message": "prompt token count " * n}}).encode())
