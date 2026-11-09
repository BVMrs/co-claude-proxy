"""Minimal repros for the crash classes found by test_rewrite_props.py.

    python3 lab/rewrite-fuzz/repro.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from copilot_shim.rewrite import Policy, rewrite  # noqa: E402

U = {"role": "user", "content": "hi"}
A = {"role": "assistant", "content": "x"}
CASES = [
    ("R1 non-string model (fnmatchcase)", "strip", True, {"model": 5, "messages": [U, A]}),
    ("R2 non-dict message as hint target", "hint", True, {"model": "claude-opus-5.5", "messages": ["hi", A]}),
    ("R3 non-iterable content in system msg", "strip", True,
     {"model": "claude-opus-5.5", "messages": [U, {"role": "system", "content": 7}]}),
    ("R3 non-iterable content in prefill (hint)", "hint", True,
     {"model": "claude-opus-5.5", "messages": [U, {"role": "assistant", "content": 7}]}),
    ("R4 text block with non-string text (hint)", "hint", True,
     {"model": "claude-opus-5.5", "messages": [U, {"role": "assistant", "content": [{"type": "text", "text": None}]}]}),
    ("S1 real answer + prefill: both dropped", "strip", True,
     {"model": "claude-opus-5.5", "messages": [U, {"role": "assistant", "content": "The answer is 42."},
                                               {"role": "assistant", "content": "{"}]}),
]

for name, mode, anth, body in CASES:
    try:
        r = rewrite(body, Policy(mode=mode), anthropic=anth)
        print(f"{name}: ok action={r.action} removed={r.removed} messages={r.body['messages']}")
    except Exception as exc:
        print(f"{name}: {type(exc).__name__}: {exc}")
