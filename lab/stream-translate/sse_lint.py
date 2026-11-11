"""Prototype: passive lint of an Anthropic SSE response as the shim relays it.

The shim never sees Copilot's OpenAI chunks, only copilot-api's translated
Anthropic events. This lint works on those bytes alone (incrementally, so it can
sit in the relay loop without buffering the response) and reports the defects
that the end-to-end runs showed to break Claude Code. Standard library only.

    lint = SSELint(); for chunk in relay: lint.feed(chunk); problems = lint.finish()

Usage (self-test over the translated scenarios): python3 sse_lint.py events.json
"""
import json
import sys


class SSELint:
    def __init__(self):
        self.buf = b""
        self.open = None          # (index, type) of the open block
        self.next = 0
        self.closed = set()
        self.tool_blocks = 0
        self.tool_ids = []
        self.stop_reason = None
        self.n_delta = self.n_stop = 0
        self.problems = []

    def _p(self, msg):
        if msg not in self.problems:
            self.problems.append(msg)

    def feed(self, data: bytes):
        self.buf += data
        while b"\n\n" in self.buf:
            frame, self.buf = self.buf.split(b"\n\n", 1)
            for line in frame.split(b"\n"):
                if line.startswith(b"data:"):
                    try:
                        self._event(json.loads(line[5:]))
                    except ValueError:
                        self._p("unparseable data line")

    def _event(self, e):
        t = e.get("type")
        if self.n_stop and t not in ("ping",):
            self._p(f"{t} after message_stop")
            return
        if t == "content_block_start":
            if self.open is not None:
                self._p("block start while another block open")
            self.open = (e.get("index"), e.get("content_block", {}).get("type"))
            self.next = (e.get("index") or 0) + 1
            if self.open[1] == "tool_use":
                self.tool_blocks += 1
                self.tool_ids.append(e["content_block"].get("id"))
        elif t == "content_block_delta":
            if self.open is None or e.get("index") != self.open[0]:
                self._p("delta to a closed block (interleaved tool arguments)"
                        if e.get("index") in self.closed else "delta to an unopened block")
        elif t == "content_block_stop":
            if self.open is not None:
                self.closed.add(self.open[0])
            self.open = None
        elif t == "message_delta":
            self.n_delta += 1
            if self.n_delta == 1:
                self.stop_reason = (e.get("delta") or {}).get("stop_reason")
        elif t == "message_stop":
            self.n_stop += 1

    def finish(self):
        if self.n_stop == 0:
            self._p("stream ended without message_stop")
        if self.n_delta > 1:
            self._p("duplicate message_delta")
        if self.stop_reason == "tool_use" and self.tool_blocks == 0:
            self._p("stop_reason tool_use without a tool_use block (tool call dropped)")
        if len(self.tool_ids) != len(set(self.tool_ids)):
            self._p("duplicate tool_use id (arguments split across blocks)")
        return self.problems


def to_sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


if __name__ == "__main__":
    events = json.load(open(sys.argv[1]))
    out = {}
    for name, ev in events.items():
        lint = SSELint()
        raw = to_sse(ev)
        for i in range(0, len(raw), 97):   # odd chunk size: frames split across reads
            lint.feed(raw[i:i + 97])
        out[name] = lint.finish()
        print(f"{name:42} {'; '.join(out[name]) or '-'}")
    if len(sys.argv) > 2:
        json.dump(out, open(sys.argv[2], "w"), indent=1)
