# co-claude-proxy

Tools for running Claude Code on a GitHub Copilot subscription through
[`ericc-ch/copilot-api`](https://github.com/ericc-ch/copilot-api).

- `copilot_shim`: a small local proxy, standard-library Python, that sits in front of
  copilot-api. Claude 4.6+ models behind Copilot answer a request ending in an
  assistant message with `400: This model does not support assistant message prefill`.
  The shim strips or rewrites that trailing message, and logs the model and
  last-message role of every request. It also turns Copilot's
  `prompt token count of N exceeds the limit of M` 400 into Anthropic's
  `prompt is too long: N tokens > M maximum`, so Claude Code compacts instead of
  failing every later turn.
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

## Tests and CI

```bash
python3 -m pip install -r requirements-dev.txt   # just pytest
python3 -m pytest -q
```

The tests run the shim and probe against an in-process fake of copilot-api
(`tests/fake_upstream.py`) on 127.0.0.1. They need no Copilot login, token or
network access. New tests must follow the same rule: use the fake, never a real
endpoint or a secret.

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs these tests with
Python 3.10 and 3.13 on every pull request and on every push to `main`. It
byte-compiles the sources first, then runs `pytest`. The workflow uses no
secrets and has read-only repository permissions. The repo has no linter or
type checker configured yet, so CI doesn't run either.
