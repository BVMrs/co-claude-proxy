"""Replay the failing request shapes against the fake Copilot upstream, directly and through the shim."""

import http.client
import json
import urllib.parse

import pytest
from conftest import FIXTURES
from fake_upstream import PREFILL_ERROR


def post(base, path, body, headers=None):
    u = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
    conn.request("POST", path, body=json.dumps(body),
                 headers={"Content-Type": "application/json", **(headers or {})})
    resp = conn.getresponse()
    data = resp.read()
    hdrs = dict(resp.getheaders())
    conn.close()
    return resp.status, hdrs, data


def get(base, path):
    u = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


IDS = [f["name"] for f in FIXTURES]


@pytest.mark.parametrize("fx", FIXTURES, ids=IDS)
def test_fixture_reproduces_400_without_shim(upstream, fx):
    status, _, data = post(upstream.url, fx["path"], fx["body"])
    assert status == 400
    assert PREFILL_ERROR in data.decode()


@pytest.mark.parametrize("mode", ["strip", "hint"])
@pytest.mark.parametrize("fx", FIXTURES, ids=IDS)
def test_fixture_succeeds_through_shim(upstream, make_shim, fx, mode):
    shim = make_shim(mode=mode)
    status, hdrs, data = post(shim, fx["path"], fx["body"])
    assert status == 200, data
    sent = upstream.received[-1][2]
    assert sent["messages"][-1]["role"] == "user"
    if fx["body"].get("stream"):
        assert hdrs.get("Content-Type") == "text/event-stream"
        text = data.decode()
        assert "event: message_start" in text and "event: message_stop" in text
    else:
        assert json.loads(data)["content"][0]["text"] == "ok"


def test_mode_off_still_fails(upstream, make_shim):
    shim = make_shim(mode="off")
    status, _, data = post(shim, FIXTURES[0]["path"], FIXTURES[0]["body"])
    assert status == 400 and PREFILL_ERROR in data.decode()


def test_prefill_ok_model_forwarded_verbatim(upstream, make_shim):
    shim = make_shim()
    body = {"model": "claude-haiku-4.5", "max_tokens": 8,
            "messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]}
    status, _, _ = post(shim, "/v1/messages", body)
    assert status == 200
    assert upstream.received[-1][2] == body


def test_headers_and_query_forwarded(upstream, make_shim):
    shim = make_shim()
    body = {"model": "claude-opus-5.5", "messages": [{"role": "user", "content": "x"}]}
    post(shim, "/v1/messages?beta=true", body, {"x-api-key": "dummy", "anthropic-version": "2023-06-01"})
    method, path, sent, headers = upstream.received[-1]
    assert path == "/v1/messages?beta=true"
    assert sent == body
    assert headers.get("x-api-key") == "dummy"
    assert headers.get("anthropic-version") == "2023-06-01"


def test_get_models_passthrough(upstream, make_shim):
    shim = make_shim()
    status, data = get(shim, "/v1/models")
    assert status == 200
    assert "claude-opus-5.5" in [m["id"] for m in json.loads(data)["data"]]


def test_model_not_supported_passed_back(upstream, make_shim):
    shim = make_shim()
    status, _, data = post(shim, "/v1/messages",
                           {"model": "claude-opus-5-5", "messages": [{"role": "user", "content": "x"}]})
    assert status == 400 and "model_not_supported" in data.decode()


def test_model_map_fixes_model_not_supported(upstream, make_shim):
    shim = make_shim(model_map={"claude-opus-5-5": "claude-opus-5.5"})
    status, _, _ = post(shim, "/v1/messages",
                        {"model": "claude-opus-5-5", "messages": [{"role": "user", "content": "x"}]})
    assert status == 200
    assert upstream.received[-1][2]["model"] == "claude-opus-5.5"


def test_upstream_down_gives_502():
    from copilot_shim.rewrite import Policy
    from copilot_shim.server import serve
    import socket
    import threading

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    server = serve("127.0.0.1", 0, f"http://127.0.0.1:{dead_port}", Policy())
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        status, _, data = post(f"http://127.0.0.1:{server.server_address[1]}", "/v1/messages",
                               {"model": "m", "messages": []})
        assert status == 502 and b"unreachable" in data
    finally:
        server.shutdown()
        server.server_close()


def test_log_line_has_model_and_last_role(upstream, make_shim, caplog):
    shim = make_shim()
    with caplog.at_level("INFO", logger="copilot_shim"):
        post(shim, FIXTURES[0]["path"], FIXTURES[0]["body"])
    line = next(r.getMessage() for r in caplog.records if "last_role=" in r.getMessage())
    assert "model=claude-opus-5.5" in line
    assert "last_role=assistant" in line
    assert "action=stripped" in line
