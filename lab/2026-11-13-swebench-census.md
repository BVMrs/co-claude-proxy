# SWE-bench Lite census against the shim (demo preparation)

Date: 2026-11-13. Track (e), offline half: the charter's demo (`demo/swebench_lite.py --n 5`) is proposed but
not built. Before it is, what does the dataset look like to the shim, and is "the first N rows" a good pick?

## Method
`lab/swebench-census/census.py` reads the real `princeton-nlp/SWE-bench_Lite` test parquet (HF revision
`6ec7bb89`, file sha256 prefix `7a21f37b8bc179c7`, 300 rows; fetched with plain `curl`, `pyarrow` only for the
lab script). It scans every `problem_statement` for content that could upset the shim or copilot-api, and runs
each one through `copilot_shim.rewrite.rewrite` (string and block content, `strip` and `hint`, anthropic shape,
trailing `{` assistant turn) with a JSON round-trip: 300 x 4 = 1200 cases. No LLM, no Copilot, no GPU.

## Results
- **Shim safe on all of it: 0/1200 failures.** No NUL, lone surrogate, emoji (astral), other control char,
  line over 2000 chars, markdown image or `data:image` (Lite excludes image issues, so the image/PDF
  findings of `attachments` do not apply to this demo), the prompt-limit phrase, or Claude tag lookalikes.
- Size: problem statement median 1043 chars (~300 tokens), p90 3188, p99 19 270, max 24 770
  (`pylint-dev__pylint-7080`, ~7k tokens). The issue text is never what approaches Copilot's prompt cap; a session's
  size comes from what Claude Code reads. The gold patch is one file in 300/300 (median 850 chars, max 4929).
- **Line endings: 165/300 statements contain `\r\n`**, 299/300 end in a newline, 39 have non-ASCII. The demo must
  pass the text byte-exact or normalise it on purpose (a `-p "$(...)"` shell round trip strips trailing newlines;
  stdin keeps them). Neither affects the proxy, but it changes prompts and so cache prefixes between runs.
- **Row order is by repo**: `--n 5` on the first rows gives `astropy__astropy-12907, -14182, -14365, -14995, -6938`,
  i.e. 5/5 from one repo (astropy has 6 of the 300). Django is 114, sympy 77 (63 % together).
- Cheapest diverse picks (smallest statement + gold patch per repo): `django__django-15061` (367+402 chars),
  `sympy__sympy-21614`, `scikit-learn__scikit-learn-13584`, `psf__requests-1963`, `pallets__flask-4045`
  (230 chars, but a 1449-char patch). Repos differ a lot in checkout size (Django/sympy/matplotlib are big), so the
  fetch script should use `git clone --filter=blob:none` then `checkout base_commit`.

## What the planner should do next
1. Pin the demo's instance list explicitly (one per repo, e.g. the five above) instead of `--n` over file order;
   if `--n` stays, sample with a fixed seed across repos. Record the HF revision and file sha256 in the fetch script.
2. Decide and test line endings: send `problem_statement` as stdin to `claude -p`, unmodified.
3. Add the 5 pinned statements (plus the `pylint-7080` max-size one) as a fixture of fake-demo prompts; the shim needs no change for them.
4. Not covered (needs a real run): how many requests a real session takes per instance; budget ~50 requests per demo
   run is tight if each session takes 10+ turns, so pick short instances and cap `--max-turns`.

Sources: Jimenez et al., SWE-bench (ICLR 2024); https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite
