# Lintro review efficacy pilot — interim findings

**Scope:** 12 historical `lgtm-hq/py-lintro` PRs with archived competitor
comments (CodeRabbit, Greptile, Macroscope, Cursor/Bugbot) vs fresh
`lintro review --depth 1 --transport cli` runs (N=2 on small/medium; N=1 on
large). Gold labels are **not** fully adjudicated yet — competitor comments are
baselines.

**PR:** https://github.com/lgtm-hq/py-lintro/pull/1962

## Setup that worked

- `CLAUDE_CODE_OAUTH_TOKEN` (`sk-ant-oat01-…`) + pinned `@anthropic-ai/claude-code@2.1.220`
- Must **not** also set `ANTHROPIC_API_KEY` to that OAuth token (CLI then fails auth)
- Ephemeral config enable via `scripts/ci/enable_review_config.py` (restored after runs)

## Wave 1 results (6 PRs × 2 runs)

| PR | Size | Bots archived | Lintro findings (run1/run2) | Verdicts | Notes |
| --- | --- | --- | --- | --- | --- |
| #916 | +1/−1 | Greptile 10, Macroscope 1 | 0 / 0 | ready / ready | Greptile/Macroscope still posted severity-tagged nits on a one-line Actions cache bump; lintro stayed quiet |
| #958 | +377/−7 | CR, Greptile, Macroscope, Bugbot\* | 3 / 1 | changes_requested / nits_only | Multi-bot PR; lintro variance high across runs |
| #1186 | +162/−29 | Greptile 16, CR, Bugbot\* | 1 / 2 | nits_only / changes_requested | Docs PR; Greptile flooded P1s on docs; lintro reported 1–2 doc-contract issues |
| #1928 | +6/−6 | CR | 1 / 0 | ready / ready | Tiny CVE bump; one speculative integrity nit then clean |
| #1936 | +52/−51 | CR | 0 / 0 | ready / ready | Crypto bump; consistently clean |
| #1958 | +385/−65 | CR | 1 / 1 | nits_only / ready | Both runs hit the same pipe-escape / backslash display nit in badge tables |

\*Cursor Bugbot comments on #958/#1186 are “not enabled for your account” stubs — not real reviews.

Approximate wave-1 spend (sum of `cost_estimate_usd`): **~$6.7**.

## Medium / large follow-ups

| PR | Size | Result | Notes |
| --- | --- | --- | --- |
| #1939 | +1522/−30 | **OK** — 1×P3, `nits_only`, ~$1.85, 1037s | Finding-model PR; quiet vs CodeRabbit’s 7 archived comments |
| #1481 | +1889/−1551 | **FAIL** `E2BIG` | CLI argv too long for prompt+diff (`ARG_MAX` ~2MB) |
| #1891 | +1577/−8 | **FAIL** timeout | Claude CLI timed out after 900s on chunk 0 |
| #1886 | +1526/−80 | **FAIL** output cap | Sonnet hit 32k `output_tokens` and CLI exited `is_error` |

Large-diff CLI transport is the main operational gap for this eval design — not finding quality.

## Early read vs competitors

1. **Noise on trivial PRs.** On #916 Greptile posted multiple P1/P2 badges (and Macroscope a High) for a Renovate Actions cache pin. Lintro produced zero findings both runs. If the goal is signal/noise on dependency bumps, lintro looks better calibrated here.
2. **Severity inflation elsewhere.** Greptile’s #1186 archive is dense with P1 badges on documentation wording. Lintro surfaced at most one P2 + P3s — closer to how a human would triage a docs PR.
3. **Real overlap on substantive PRs.** On #958, CodeRabbit/Greptile/Macroscope all touched `astro_check.py` non-interactive behavior; lintro also focused there (test gaps / shim fallback). Same neighborhood, different framing.
4. **Run-to-run variance is real.** #958 went 3 findings → 1 finding; #1186 flipped verdict nits↔changes_requested. Any comparison needs N≥3 and median stats before claiming wins.
5. **Historical Bugbot is often unusable as a baseline** in this repo’s older PRs (account-not-enabled stubs). Prefer live Bugbot or drop it from the corpus.
6. **CLI transport ceiling on large PRs.** Three failure modes showed up above ~1.5k-line diffs: `E2BIG` (argv), wall-clock timeout, and 32k output-token exhaustion while emitting the mandatory checklist JSON. For efficacy work, prefer path-filtered / chunked reviews — or API transport — on large PRs.

## What this does *not* prove yet

- Precision/recall against human must-catch labels (draft `gold.candidates.json` files exist per wave-1 PR; need adjudication)
- Depth 2/3 or `--with-lint` deltas
- Behavior on non-py-lintro codebases
- Head-to-head against freshly re-run competitors (archives only)

## Next steps

1. Finish large-PR wave; path-filter or chunk PRs that hit `E2BIG`
2. Human-label `gold.candidates.json` for #958, #1186, #1958, #1916 (highest information)
3. Add depth-2 + `--with-lint` on the 4 densest PRs
4. Optionally expand corpus to a second repo once labels exist
