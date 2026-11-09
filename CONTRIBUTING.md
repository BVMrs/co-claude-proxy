# Contributing to co-claude-proxy

This file describes how work lands in this repository. Agents and `pr-create` read it.
The project is `copilot_shim`, a standard-library Python proxy plus a probe, with
pytest tests. See [README.md](README.md) and [docs/SETUP.md](docs/SETUP.md).

## 1. Branch naming

- The default branch is `main`. Never commit to it directly; all changes arrive by PR.
- Agent branches are `claude/<slug>`, where the slug is short kebab-case and names the
  task: `claude/ci-test-workflow`, `claude/copilot-proxy-prefill`, `claude/add-contributing`.
  Branches created from a chat thread got a generated slug (`claude/project-thread-8v3t9d`).
- One branch per task. Don't reuse a merged branch for new work.

## 2. Commit messages

The format in `git log`:

- Subject: imperative mood, capitalized, no trailing period, no type prefix
  (`feat:` etc. is not used). It says what the commit does to the code:
  `Add probe: list model IDs and test prefill support per model`,
  `Fold trailing role:system messages into the system field`.
- Body (optional, after a blank line): why the change was needed and what it changes,
  wrapped at about 72 columns. Use it whenever the reason isn't obvious from the subject.
- Commits written by an agent end with a `Co-Authored-By:` trailer naming the model.
- Small, self-contained commits: one step each (rewrite logic, then server, then tests, then docs).

## 3. PR process

- **CI must pass.** The workflow is [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
  (`CI`, job `pytest (Python <version>)`). It runs on every pull request and every push
  to `main`, with Python 3.10 and 3.13, and executes:

  ```bash
  python -m pip install -r requirements-dev.txt
  python -m compileall -q copilot_shim tests
  python -m pytest -q
  ```

  It uses no secrets and has read-only permissions. There is no linter or type checker.
- **Description** must contain:
  - **What / why**: the change and the problem it solves.
  - **How verified**: commands run and their result (e.g. `73 passed`), plus any manual
    check against a running shim.
  - **Risks**: what could break, and follow-ups left open.
- **Approval**: one approval from the owner (BVMrs). Agents never merge, and never
  approve their own PR.
- **Merging** is squash merge, done by the owner. After each merge the runner rebases the
  other open agent PRs onto `main`, so keep branches rebase-friendly (no merge commits
  from `main` into a feature branch).
- Agents don't push or open PRs themselves: the runner pushes the branch and opens the PR
  with `.agent-work/summary.md` as the description. `.agent-work/` is gitignored and removed
  before the PR.

## 4. PR sizing

- One concern per PR: a bug fix, a feature, a doc change. Not a mix.
- Aim for under ~400 changed lines (`git diff --stat origin/main...HEAD`). Split larger work into
  PRs that each pass CI on their own, e.g. rewrite logic first, server wiring second.
- Don't stack a PR on another unmerged PR unless the owner asks for it. Branch from `main`.

## 5. Shared files

- Keep edits to `README.md`, `docs/HANDOVER.md`, `docs/SETUP.md`, and (if they are added)
  `CLAUDE.md`, `docs/ROADMAP.md`, `docs/ARCHITECTURE.md` out of feature PRs unless the
  task is about them. Put suggested edits in the PR description instead. These files are
  touched by many tasks and cause merge conflicts between parallel PRs.
  Exception: a PR that changes a CLI flag or user-visible behaviour updates the matching
  section of `docs/SETUP.md` (see section 8).
- Dependency lists (`requirements-dev.txt`, and any future `pyproject.toml` or similar):
  one entry per line, sorted. The shim itself must stay standard-library only; only test
  tooling goes in `requirements-dev.txt`.
- Never commit credentials, Copilot tokens or real account data. Captured requests go into
  fixtures only after credentials are removed.

## 6. Testing conventions

- **Framework**: pytest (`pytest>=7`, from `requirements-dev.txt`). No pytest config file;
  run from the repository root.
- **Location and naming**: `tests/test_<area>.py` (`test_rewrite.py`, `test_replay.py`,
  `test_probe.py`, `test_system_role.py`); test functions are `test_<behaviour>` and read
  as a sentence (`test_upstream_down_gives_502`).
- **Shared setup** is in `tests/conftest.py`: it puts the repo root and `tests/` on
  `sys.path`, loads `FIXTURES`, and provides the `upstream` fixture (a fresh
  `FakeUpstream`) and `make_shim(**policy_kwargs)` (starts a shim on `127.0.0.1:0` and
  shuts it down after the test).
- **Run**:

  ```bash
  python3 -m pip install -r requirements-dev.txt          # once (pytest only)
  python3 -m pytest -q                                    # all tests
  python3 -m pytest -q tests/test_rewrite.py              # one file
  python3 -m pytest -q tests/test_rewrite.py::test_bad_mode  # one test
  python3 -m pytest -q -k system_role                     # by keyword
  ```

- **Mocking strategy**: no mocking library. Network behaviour is tested against
  `tests/fake_upstream.py`, an in-process HTTP server that reproduces the Copilot behaviour
  recorded in `docs/HANDOVER.md` (model list, `model_not_supported`, prefill 400). If the
  real upstream behaves differently, change the fake to match and say so in the PR. Use
  pytest's own `capsys`, `caplog` and `@pytest.mark.parametrize`.
- **Never** call a real endpoint, need a Copilot login, or use a secret in a test. CI has
  no network credentials, and tests must pass offline.
- **What to test**:
  - Pure rewrite logic (`copilot_shim.rewrite`) directly: the resulting body, `action`,
    counts, and that the input isn't mutated.
  - End-to-end through a real shim and the fake: status code, what the upstream received,
    streamed (SSE) and non-streamed replies, log lines.
  - Each request shape in `tests/fixtures/prefill_requests.json` must fail directly against
    the fake and succeed through the shim in `strip` and `hint` mode. New failing shapes
    go there as a fixture, with credentials removed.
  - A bug fix comes with a test that fails before the fix.
- **What not to test**: copilot-api itself, real Copilot model behaviour, or exact log
  wording beyond the fields the docs promise (`model=`, `last_role=`, `action=`).
- Never edit a test just to make it pass; a failing test is evidence.

## 7. Review

- Each PR gets an automated review. It **blocks** only on Critical or High findings, or on
  failing tests (locally or in CI).
- Medium and Low findings are follow-up notes: fix them in the PR if cheap, otherwise list
  them under follow-ups in the description. They don't block merging.
- The owner's approval is still required (section 3).

## 8. Definition of done

- [ ] `python3 -m compileall -q copilot_shim tests` succeeds.
- [ ] `python3 -m pytest -q` passes locally, and CI passes on both Python versions.
- [ ] New behaviour or a bug fix has a test; no real endpoint or secret is used.
- [ ] Docs the change touches are updated: flags, log fields or behaviour in
      `docs/SETUP.md`; the shim stays standard-library only.
- [ ] Commits follow section 2; the branch is `claude/<slug>` off `main`.
- [ ] The PR description has what/why, how verified, and risks/follow-ups.
- [ ] The diff is one concern and stays under ~400 changed lines, or the split is explained.
