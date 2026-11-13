"""Copilot's prompt-token-limit 400 is rewritten into Anthropic's "prompt is too long" error."""

import json

import pytest
from test_replay import post

from copilot_shim.errors import rewrite_prompt_too_long

COPILOT_INNER = {"error": {"message": "prompt token count of 132456 exceeds the limit of 128000",
                           "code": "model_max_prompt_tokens_exceeded"}}
# What copilot-api 0.7.0 forwards (src/lib/error.ts forwardError): Copilot's JSON as a string.
COPILOT_FORWARDED = json.dumps({"error": {"message": json.dumps(COPILOT_INNER), "type": "error"}}).encode()
EXPECTED = {"type": "error", "error": {"type": "invalid_request_error",
                                       "message": "prompt is too long: 132456 tokens > 128000 maximum"}}
BODY = {"model": "claude-opus-5.5", "max_tokens": 8, "messages": [{"role": "user", "content": "x"}]}
MESSAGES = "/v1/messages"


@pytest.mark.parametrize("raw", [
    COPILOT_FORWARDED,
    json.dumps(COPILOT_INNER).encode(),
    json.dumps({"error": {"message": "Prompt token count 132456 exceeds the limit 128000"}}).encode(),
], ids=["copilot-api-forwarded", "copilot-direct", "loose-wording"])
def test_rewrite_parses_counts(raw):
    assert json.loads(rewrite_prompt_too_long(400, raw)) == EXPECTED


def test_code_without_numbers_keeps_original_text():
    raw = json.dumps({"error": {"message": "Request too large", "code": "model_max_prompt_tokens_exceeded"}})
    out = json.loads(rewrite_prompt_too_long(400, raw.encode()))
    assert out["error"] == {"type": "invalid_request_error", "message": "prompt is too long: Request too large"}


def test_phrase_without_numbers_keeps_original_text():
    raw = json.dumps({"error": {"message": "prompt token count exceeds the limit", "type": "error"}})
    out = json.loads(rewrite_prompt_too_long(400, raw.encode()))
    assert out["error"]["message"] == "prompt is too long: prompt token count exceeds the limit"


@pytest.mark.parametrize("status,raw", [
    (400, json.dumps({"error": {"message": "The requested model is not supported.",
                                "code": "model_not_supported"}}).encode()),
    (413, COPILOT_FORWARDED),
    (500, COPILOT_FORWARDED),
    (400, b"prompt token count of 5 exceeds the limit of 4"),      # not JSON
    (400, b'{"error": "prompt token count of 5'),                   # truncated JSON
    (400, b'["prompt token count of 5 exceeds the limit of 4"]'),  # JSON but not an object
    (400, b""),
], ids=["other-400", "413", "500", "not-json", "truncated", "array", "empty"])
def test_other_errors_not_rewritten(status, raw):
    assert rewrite_prompt_too_long(status, raw) is None


CANNED_LIMIT = {"canned": {"claude-opus-5.5": (400, COPILOT_FORWARDED)}}
CANNED_LIMIT_TEXT = {"canned": {"claude-opus-5.5": (400, COPILOT_FORWARDED, "text/plain; charset=utf-8")}}


@pytest.mark.parametrize("upstream", [CANNED_LIMIT, CANNED_LIMIT_TEXT], indirect=True, ids=["json", "text-ctype"])
@pytest.mark.parametrize("stream", [False, True])
def test_shim_rewrites_prompt_limit_error(upstream, make_shim, stream):
    shim = make_shim()
    status, hdrs, data = post(shim, MESSAGES, {**BODY, "stream": stream})
    assert status == 400
    assert hdrs["Content-Type"] == "application/json"
    assert int(hdrs["Content-Length"]) == len(data)
    assert json.loads(data) == EXPECTED


@pytest.mark.parametrize("upstream", [CANNED_LIMIT], indirect=True)
def test_shim_logs_original_error(upstream, make_shim, caplog):
    shim = make_shim()
    with caplog.at_level("INFO", logger="copilot_shim"):
        post(shim, MESSAGES, BODY)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "exceeds the limit of 128000" in text
    assert "rewrote prompt-token-limit error" in text


OTHER_400 = json.dumps({"error": {"message": "something else", "code": "invalid_request_body"}}).encode()
MALFORMED = b"prompt token count of 132456 exceeds the limit of 128000 <html>"


@pytest.mark.parametrize("upstream", [{"canned": {"claude-opus-5.5": (400, OTHER_400)}},
                                      {"canned": {"claude-opus-5.5": (400, MALFORMED)}},
                                      {"canned": {"claude-opus-5.5": (500, COPILOT_FORWARDED)}}],
                         indirect=True, ids=["other-400", "malformed-400", "500"])
def test_shim_passes_other_errors_byte_identical(upstream, make_shim):
    shim = make_shim()
    status, hdrs, data = post(shim, MESSAGES, BODY)
    expected_status, expected, *_ = upstream.canned["claude-opus-5.5"]
    assert status == expected_status
    assert data == expected
    assert int(hdrs["Content-Length"]) == len(expected)
