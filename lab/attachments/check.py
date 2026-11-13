"""Run captured Claude Code bodies (main shim output) through copilot-api 0.7.0's translateToOpenAI,
with and without proto.hoist_attachments, and check the OpenAI payload:
  - images_in_nonuser : image_url parts in a message whose role is not "user" (OpenAI 400s these)
  - images_total      : image_url parts that reach Copilot at all
  - pdf_bytes_lost    : base64 chars of document blocks that do not appear anywhere in the payload
  - order_ok          : every tool message directly follows the assistant tool_calls / other tool msgs
Usage (repo root): python3 lab/attachments/check.py <copilot-api dir> <body.json> [...]
"""
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from proto import hoist_attachments  # noqa: E402

api = pathlib.Path(sys.argv[1]).resolve()
TS = HERE.parent / "copilot-translate" / "translate.ts"


def translate(bodies):
    inp = "".join(json.dumps(b) + "\n" for b in bodies)
    out = subprocess.run(["bun", str(TS), str(api / "src/routes/messages/non-stream-translation.ts")],
                         input=inp, capture_output=True, text=True, check=True).stdout
    return [json.loads(x) for x in out.splitlines()]


def docs(body):
    found = []
    def walk(c):
        for b in c if isinstance(c, list) else []:
            if isinstance(b, dict):
                if b.get("type") == "document":
                    found.append(b["source"].get("data", ""))
                walk(b.get("content"))
    for m in body["messages"]:
        walk(m.get("content"))
    return found


def check(body, payload):
    s = json.dumps(payload)
    msgs = payload["messages"]
    nonuser = sum(1 for m in msgs if m["role"] != "user" and isinstance(m.get("content"), list)
                  for p in m["content"] if p.get("type") == "image_url")
    total = sum(1 for m in msgs if isinstance(m.get("content"), list) for p in m["content"] if p.get("type") == "image_url")
    lost = sum(len(d) for d in docs(body) if d and d not in s)
    order = all(i > 0 and (msgs[i - 1]["role"] == "tool" or msgs[i - 1].get("tool_calls"))
                for i, m in enumerate(msgs) if m["role"] == "tool")
    return {"images_in_nonuser": nonuser, "images_total": total, "pdf_b64_chars_lost": lost, "order_ok": order}


rows = []
for path in sys.argv[2:]:
    body = json.load(open(path))
    fixed, st = hoist_attachments(body)
    a, b = translate([body, fixed])
    rows.append({"body": path, "main": check(body, a["ok"]), "proto": check(body, b["ok"]), "proto_stats": st})
    print(json.dumps(rows[-1]))
json.dump(rows, open(HERE / "results.json", "w"), indent=1)
