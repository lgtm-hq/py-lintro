# Review efficacy eval (pilot)

Offline head-to-head harness for `lintro review` vs archived competitor PR
comments (CodeRabbit, Greptile, Macroscope, Cursor/Bugbot) on historical
`lgtm-hq/py-lintro` pull requests.

This is **not** part of the unit/integration suite. Runs are paid LLM calls
(`CLAUDE_CODE_OAUTH_TOKEN` + pinned `claude` CLI) and are intentionally manual.

## Layout

```
evals/review-efficacy/
  corpus/                 # PR fixtures (meta + normalized competitor findings)
  runs/<stamp>/           # lintro review JSON outputs per config/run
  reports/                # human-readable summaries
  scripts/
    build_corpus.py       # fetch PR meta + bot comments via gh
    run_eval.py           # invoke lintro review N times per case
    score_eval.py         # overlap / severity tables (+ draft gold labels)
```

## Prerequisites

```bash
export PATH="$HOME/.local/bin:$PATH"
export CLAUDE_CODE_OAUTH_TOKEN=...   # Claude Code OAuth (sk-ant-oat01-...)
# Do NOT set ANTHROPIC_API_KEY to the OAuth token — it breaks CLI auth.

CLAUDE_CODE_VERSION=$(python3 scripts/ci/ai_tools_arg_pin.py CLAUDE_CODE_VERSION)
npm config set prefix "$HOME/.local"
npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"
```

## Workflow

```bash
# 1) Build / refresh corpus for selected PRs
uv run python evals/review-efficacy/scripts/build_corpus.py \
  --prs 1928,1936,1904,1958,1891,1939,1886,1916,1878,1575

# 2) Run lintro review (patches .lintro-config.yaml ephemerally, restores after)
uv run python evals/review-efficacy/scripts/run_eval.py \
  --depth 1 --runs 2 --timeout 900

# 3) Score against competitor archives + write report draft
uv run python evals/review-efficacy/scripts/score_eval.py \
  --run-dir evals/review-efficacy/runs/<stamp>
```

## Scoring notes

Competitor comments are **baselines**, not ground truth. `score_eval.py` reports
location overlap and severity histograms, and emits a draft `gold.candidates.json`
per PR for human adjudication (must-catch / should-catch / noise).
