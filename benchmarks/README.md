# Comparative benchmarks

A reproducible, locally-runnable harness that measures lintro's wall-clock performance
against other meta-linters — **MegaLinter**, **pre-commit**, and raw **sequential native
tool invocation**. It turns "lintro is fast" into a number you can reproduce and audit.

This implements [#1055](https://github.com/lgtm-hq/py-lintro/issues/1055).

## Quick start

```bash
# Benchmark lintro against every competitor available on this machine.
uv run python -m benchmarks.run --runs 5

# Restrict to specific competitors / scenarios / fixtures.
uv run python -m benchmarks.run \
  --include lintro --include sequential \
  --scenario full_check_warm \
  --fixture small-python \
  --runs 10
```

Outputs are written to `benchmarks/results/`:

- `latest.json` — full report (metadata + per-run samples), stable schema.
- `latest.md` — markdown comparison table (also printed to stdout).

## Graceful degradation

Competitor tools are frequently not installed. The harness **detects what is available
and benchmarks only that**, always including lintro as the baseline. Skipped competitors
are recorded in the report's `notes` so coverage is never silently overstated.
Concretely:

| Competitor        | Detected via                                    | If missing     |
| ----------------- | ----------------------------------------------- | -------------- |
| lintro            | always (run through `uv run lintro`)            | —              |
| sequential-native | always (uses lintro-managed tools via `uv run`) | —              |
| pre-commit        | `pre-commit` or `prek` on `PATH`                | skipped, noted |
| MegaLinter        | `mega-linter-runner`, else `docker` CLI         | skipped, noted |

So on a machine with only lintro installed you still get a valid — if lintro-only —
report, and the notes tell you exactly what was left out.

## Methodology

- **Timing** — `time.perf_counter()` around a captured subprocess. Each command runs
  `--runs` times (default 5); we report **min, max, mean, median, and sample standard
  deviation**. The **median** is the headline figure because it is robust to occasional
  outliers (GC pauses, scheduler jitter).
- **Cold vs. warm** — the `full_check_cold` scenario times the very first invocation
  with no warmup; `full_check_warm` discards one priming run first so filesystem and
  interpreter caches are hot before timing begins.
- **Relative column** — each tool's median is expressed as a multiple of lintro's median
  for the same fixture/scenario. `2.00x` means "twice as slow as lintro"; values below
  `1.00x` mean the competitor was faster — reported honestly, no cherry-picking.
- **Apples-to-apples tool selection** — every competitor is configured to run the _same_
  underlying linters (ruff check + ruff format) as lintro on the fixture, so the
  comparison isolates **orchestration overhead** rather than differing tool sets.
  Configs live in `benchmarks/configs/` and are version-pinned.
- **Reproducibility metadata** — every report records the git SHA, platform, Python
  version, CPU count, run count, and skipped competitors.

## Fixtures

`benchmarks/fixtures/` holds pinned, self-contained target projects. Today:

- `small-python/` — a small, clean Python-only project (passes ruff), so timing reflects
  startup and traversal cost rather than variable diagnostic volume.

Add medium-polyglot and large-monorepo fixtures (or a pinned public OSS repo, as the
issue suggests) by dropping new directories under `fixtures/`; they are auto-discovered.

## Running the full comparison

The reference numbers in this repo were taken with lintro and sequential-native only,
because MegaLinter and pre-commit are not installed in CI's default image. To reproduce
the complete comparison locally:

```bash
# pre-commit
uv tool install pre-commit            # or: pipx install pre-commit

# MegaLinter (either wrapper)
npm install -g mega-linter-runner     # or rely on the docker CLI + pinned image

uv run python -m benchmarks.run --runs 5
```

Pinned competitor versions:

- ruff — `v0.15.9` (see `configs/.pre-commit-config.yaml`)
- MegaLinter image — `oxsecurity/megalinter-python:v8.9.0` (see `harness/runners.py`)

Bump these deliberately and re-run to refresh the published numbers.

## Sample results

Illustrative run on Apple Silicon (macOS, 10 logical CPUs, `--runs 3`, lintro +
sequential-native only). **Timings are machine-specific** — treat the _relative_ column
as the portable signal.

| Fixture      | Scenario        | Tool              | Median  | Relative |
| ------------ | --------------- | ----------------- | ------- | -------- |
| small-python | full_check_cold | sequential-native | 0.102 s | 0.20x    |
| small-python | full_check_cold | lintro            | 0.502 s | 1.00x    |
| small-python | full_check_warm | sequential-native | 0.100 s | 0.20x    |
| small-python | full_check_warm | lintro            | 0.509 s | 1.00x    |

On this tiny fixture raw sequential ruff wins: lintro pays a fixed Python-interpreter
and plugin-discovery startup cost that dominates when there is almost nothing to lint.
That cost amortizes as the fixture grows and the tool count rises — which is exactly why
medium/polyglot and large/monorepo fixtures (and the MegaLinter/pre-commit competitors,
which carry their own heavier startup) are the interesting comparisons to run next. The
harness reports these losses without spin, per the issue's requirement.

## Programmatic use

```python
from benchmarks.harness import summarize, render_markdown_table, BenchmarkReport

stats = summarize([0.51, 0.49, 0.50])
print(stats.median_s)  # 0.50

report = BenchmarkReport.from_json(open("benchmarks/results/latest.json").read())
print(render_markdown_table(report))
```
