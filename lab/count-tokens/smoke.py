"""Smoke test: a few bodies against count_server.ts. Usage: python3 smoke.py <port>"""
import base64
import json
import os
import sys
import urllib.request

URL = f"http://127.0.0.1:{sys.argv[1]}" + "/v1/messages/count_tokens"


def count(body):
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req))["input_tokens"]


img = base64.b64encode(os.urandom(30000)).decode()
print("short text", count({"model": "claude-opus-5.5", "messages": [{"role": "user", "content": "hello world, this is a test"}]}))
print("hyphen id", count({"model": "claude-opus-5-5", "messages": [{"role": "user", "content": "hello world"}]}))
print("30KB image", count({"model": "claude-opus-5.5", "messages": [{"role": "user", "content": [
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img}}]}]}))
