import json

from fake_upstream import MODELS, FakeUpstream

from copilot_shim import probe


def test_list_models(upstream):
    assert probe.list_models(upstream.url, "dummy") == sorted(MODELS)


def test_probe_matches_handover_findings(upstream):
    got = {m: probe.probe_model(upstream.url, "dummy", m).verdict
           for m in MODELS if m.startswith("claude")}
    assert got == {
        "claude-haiku-4.5": "accepts-prefill",
        "claude-haiku-5.5": "rejects-prefill",
        "claude-opus-4.8": "rejects-prefill",
        "claude-opus-5": "rejects-prefill",
        "claude-opus-5.5": "rejects-prefill",
        "claude-sonnet-5": "rejects-prefill",
        "claude-sonnet-5.5": "rejects-prefill",
    }


def test_invalid_model_id_is_unusable(upstream):
    r = probe.probe_model(upstream.url, "dummy", "claude-opus-5-5")
    assert r.verdict == "model-unusable"
    assert r.prefill_status is None
    assert "model_not_supported" in r.detail


def test_main_json_default_filters_to_claude(upstream, capsys):
    rc = probe.main(["--base-url", upstream.url, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [r["model"] for r in out] == sorted(m for m in MODELS if "claude" in m)


def test_main_list(upstream, capsys):
    assert probe.main(["--base-url", upstream.url, "--list"]) == 0
    assert capsys.readouterr().out.split() == sorted(MODELS)


def test_main_text_suggests_shim_flags(upstream, capsys):
    probe.main(["--base-url", upstream.url, "--model", "claude-haiku-4.5", "--model", "claude-opus-5.5"])
    out = capsys.readouterr().out
    assert "--prefill-ok claude-haiku-4.5" in out
    assert "--prefill-ok claude-opus-5.5" not in out


def test_main_warns_on_unknown_model(upstream, capsys):
    probe.main(["--base-url", upstream.url, "--model", "claude-nope"])
    assert "not in /v1/models" in capsys.readouterr().err


def test_server_error_reported_as_error(capsys):
    with FakeUpstream(broken_models={"claude-opus-5"}) as up:
        r = probe.probe_model(up.url, "dummy", "claude-opus-5")
    assert r.verdict == "error" and r.baseline_status == 500


def test_unreachable_base_url(capsys):
    assert probe.main(["--base-url", "http://127.0.0.1:1"]) == 2
    assert "error:" in capsys.readouterr().err
