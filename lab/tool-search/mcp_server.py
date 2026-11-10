"""Minimal stdio MCP server (no deps) exposing N synthetic tools.

Usage: python3 mcp_server.py <n_tools>
Tool names: lab_tool_00..NN, plus three long names (40, 55, 70 chars) to probe the 64-char
OpenAI function-name limit once Claude Code prefixes them with mcp__<server>__.
"""
import json
import sys

N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
NAMES = [f"lab_tool_{i:02d}" for i in range(N)]
NAMES += ["long_" + "x" * 35, "long_" + "y" * 50, "long_" + "z" * 65]


def tool(name):
    return {"name": name,
            "description": f"Synthetic lab tool {name}. Returns the word PONG and echoes its argument. "
                           + "Padding sentence for a realistic description length. " * 4,
            "inputSchema": {"type": "object", "properties": {
                "arg": {"type": "string", "description": "anything", "format": "uri"},
                "count": {"type": "integer", "minimum": 0, "default": 1}},
                "required": ["arg"], "additionalProperties": False}}


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
        reply(i, {"tools": [tool(n) for n in NAMES]})
    elif meth == "tools/call":
        p = m["params"]
        reply(i, {"content": [{"type": "text", "text": f"PONG {p['name']} {json.dumps(p.get('arguments'))}"}]})
    else:
        reply(i, {})
