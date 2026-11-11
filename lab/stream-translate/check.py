"""Check translated Anthropic event streams: protocol grammar and content fidelity.

Grammar (Anthropic Messages streaming docs): message_start once, first; each block is
content_block_start(i) -> deltas(i) -> content_block_stop(i), with i = 0, 1, 2, ...;
deltas only target the open block and match its type; then message_delta once;
message_stop once, last.
Fidelity: rebuild the message as an accumulator would (text joined, tool input =
concatenated partial_json parsed as JSON) and compare with the intended turn.
Usage: python3 check.py <events.json>   (scenarios.py supplies the intended turns)
"""
import json
import sys

from scenarios import build


def grammar(ev):
    errs = []
    types = [e["type"] for e in ev]
    if not types or types[0] != "message_start":
        errs.append("no leading message_start")
    if types.count("message_start") > 1:
        errs.append("message_start x%d" % types.count("message_start"))
    if types.count("message_delta") != 1:
        errs.append("message_delta x%d" % types.count("message_delta"))
    if types.count("message_stop") != 1:
        errs.append("message_stop x%d" % types.count("message_stop"))
    elif types[-1] != "message_stop":
        errs.append("events after message_stop")
    open_i, open_type, next_i, closed = None, None, 0, set()
    for e in ev:
        t = e["type"]
        if t == "content_block_start":
            if open_i is not None:
                errs.append(f"start {e['index']} while {open_i} open")
            if e["index"] != next_i:
                errs.append(f"start index {e['index']} expected {next_i}")
            open_i, open_type, next_i = e["index"], e["content_block"]["type"], e["index"] + 1
        elif t == "content_block_delta":
            if e["index"] != open_i:
                errs.append(f"delta to {'closed' if e['index'] in closed else 'unopened'} block {e['index']}")
            elif (e["delta"]["type"] == "text_delta") != (open_type == "text"):
                errs.append("delta type mismatch")
        elif t == "content_block_stop":
            if e["index"] != open_i:
                errs.append(f"stop for non-open block {e['index']}")
            closed.add(e["index"]); open_i = None
        elif t == "message_delta" and open_i is not None:
            errs.append(f"message_delta with block {open_i} open")
    if open_i is not None:
        errs.append(f"block {open_i} never stopped")
    return sorted(set(errs), key=errs.index)


def rebuild(ev):
    blocks, stop = {}, None
    for e in ev:
        if e["type"] == "content_block_start":
            b = dict(e["content_block"]); b["_json"] = ""; blocks[e["index"]] = b
        elif e["type"] == "content_block_delta" and e["index"] in blocks:
            d = e["delta"]
            if d["type"] == "text_delta":
                blocks[e["index"]]["text"] += d["text"]
            else:
                blocks[e["index"]]["_json"] += d["partial_json"]
        elif e["type"] == "message_delta" and stop is None:
            stop = e["delta"]["stop_reason"]
    text, tools = "", []
    for i in sorted(blocks):
        b = blocks[i]
        if b["type"] == "text":
            text += b["text"]
        else:
            try:
                inp = json.loads(b["_json"]) if b["_json"] else {}
            except ValueError:
                inp = "<invalid JSON: %r>" % b["_json"][:40]
            tools.append({"id": b["id"], "name": b["name"], "input": inp})
    return text, tools, stop


def fidelity(intended, text, tools, stop):
    errs = []
    if text != intended["text"]:
        errs.append(f"text {text!r} != {intended['text']!r}")
    want = [(t["id"], json.loads(t["args"])) for t in intended["tools"]]
    got = [(t["id"], t["input"]) for t in tools]
    if intended.get("truncated"):
        # The native API also ends a max_tokens turn with a partial tool input.
        want = [(i, g) for (i, g) in got]
    if got != want:
        lost = [i for i, _ in want if i not in [g for g, _ in got]]
        if lost:
            errs.append("tool calls lost: " + ",".join(lost))
        for (gi, gin) in got:
            w = dict(want).get(gi)
            if w is not None and gin != w:
                errs.append(f"{gi} input wrong: {str(gin)[:70]}")
        if len(got) != len(set(g for g, _ in got)):
            errs.append("duplicate tool ids")
    exp_stop = "tool_use" if intended["tools"] else "end_turn"
    if intended.get("truncated"):
        exp_stop = "max_tokens"
    if intended.get("filtered"):
        exp_stop = "refusal or error"
    if stop != exp_stop:
        errs.append(f"stop_reason {stop} (want {exp_stop})")
    return errs


def main(path):
    events = json.load(open(path))
    _, intended, notes = build()
    report = {}
    for name, ev in events.items():
        if isinstance(ev, dict):
            report[name] = {"throw": ev["throw"]}
            continue
        g = grammar(ev)
        text, tools, stop = rebuild(ev)
        f = fidelity(intended[name], text, tools, stop)
        report[name] = {"note": notes[name], "events": len(ev), "grammar": g, "fidelity": f,
                        "tools_got": [t["id"] for t in tools], "stop": stop}
        flag = "OK " if not g and not f else "BAD"
        print(f"{flag} {name:42} ev={len(ev):2} stop={stop}")
        for x in g:
            print("      grammar:", x)
        for x in f:
            print("      fidelity:", x)
    return report


if __name__ == "__main__":
    rep = main(sys.argv[1])
    if len(sys.argv) > 2:
        json.dump(rep, open(sys.argv[2], "w"), indent=1)
