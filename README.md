# co-claude-proxy

Tools for running Claude Code on a GitHub Copilot subscription through
[`ericc-ch/copilot-api`](https://github.com/ericc-ch/copilot-api).

- `copilot_shim`: a small local proxy, standard-library Python, that sits in front of
  copilot-api. Claude 4.6+ models behind Copilot answer a request ending in an
  assistant message with `400: This model does not support assistant message prefill`.
  The shim strips or rewrites that trailing message, and logs the model and
  last-message role of every request.
- `copilot_shim.probe`: lists the valid model IDs and probes which models accept
  prefill.

```bash
npx copilot-api@latest start                 # :4141
python3 -m copilot_shim.probe                # which models accept prefill?
python3 -m copilot_shim                      # :4142 -> :4141
ANTHROPIC_BASE_URL=http://localhost:4142 ANTHROPIC_AUTH_TOKEN=dummy claude
```

Full setup, flags and troubleshooting: [docs/SETUP.md](docs/SETUP.md).
Investigation history: [docs/HANDOVER.md](docs/HANDOVER.md).

These proxies are not supported by GitHub. Check GitHub's terms before using one.
No credentials belong in this repo.

Tests: `python3 -m pytest -q`
