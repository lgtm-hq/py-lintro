# Review agreement matrix

Offline eval that answers "does switching provider/model change what `lintro review`
finds?" — issue #2147, extending the #1962 pilot.

For each `(provider, model, transport)` config in `matrix.yaml`, the runner executes N
repeated `lintro review` invocations over every item in `corpus/corpus.yaml`, then
reports:

1. **Stability** — per-config verdict flip rate and finding-set Jaccard across the
   repeats. This is the noise floor.
2. **Agreement** — cross-config finding-level match rate and verdict agreement, always
   printed next to both configs' noise floors. An agreement gap smaller than the noise
   floor is not a difference between the configs.
3. **Efficacy** — precision/recall against `expected_findings` labels, for the corpus
   items that carry them.

This harness is **not** part of the package or the test suite. It lives in `evals/`, it
is never packaged into the wheel or sdist, and running it costs real inference money.

## Layout

```text
evals/review-efficacy/
  matrix.yaml            # committed (provider, model, transport) configs
  corpus/corpus.yaml     # committed corpus items + optional labels
  run_matrix.py          # entry point
  review_matrix/         # harness package (imported by tests/evals/)
  runs/<stamp>/          # per-run raw payloads + report.json + report.md
```

## Spend gate

Runs are paid inference. The command **never** spends money by accident:

```bash
# 1) Dry run: prints the projected spend table and exits 0.
uv run python evals/review-efficacy/run_matrix.py

# 2) Same command with the confirmation flag actually executes the matrix.
uv run python evals/review-efficacy/run_matrix.py --confirm-spend
```

The projection multiplies `repeats × corpus items` by each config's
`projected_cost_usd`, and also prints the ceiling from its `max_cost_usd`. Every
invocation is capped at that ceiling via `LINTRO_AI_MAX_COST_USD`, so the ceiling column
is the worst case, not a guess.

## How configs are driven

Only the documented environment overrides (`lintro/ai/config_overrides.py`) select a
provider:

- `LINTRO_AI_PROVIDER`
- `LINTRO_AI_MODEL`
- `LINTRO_AI_TRANSPORT`
- `LINTRO_AI_MAX_COST_USD`

There is no code-side provider wiring in the harness, so a matrix run measures the
shipped CLI rather than a harness-specific path through it. Provider credentials come
from your environment as usual, and `ai.review` must be enabled in the checkout's
`.lintro-config.yaml`.

## Output

Each run writes `runs/<stamp>/`:

- `<config-id>/<item-id>/run-<n>.json` — the raw `--output json` payload (and
  `run-<n>.stderr.txt` when the invocation wrote to stderr)
- `report.json` — every run plus all three metric blocks
- `report.md` — the same numbers as markdown tables

Pass `--stamp` to name the run directory yourself; otherwise it is a UTC timestamp.
Nothing else in a report depends on the clock: the only non-deterministic value recorded
is each run's elapsed time.

## Adding labels

Add `expected_findings` to a corpus item to bring it into the efficacy table:

```yaml
- id: pr-1928
  pr: 1928
  expected_findings:
    - file: lintro/ai/review/orchestrator.py
      category: correctness
      title: Cost cap is checked after the request is issued
      severity: P1
```

Labels are adjudicated by a human. Competitor bot comments (archived by the #1962 pilot)
are candidates for adjudication, never ground truth.

## Tests

`tests/evals/` covers the metric functions on synthetic finding sets and the runner
against a mocked invoker. No test in that directory touches a provider or the network.
