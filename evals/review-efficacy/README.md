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
- `runs.jsonl` — one JSON record per run, appended as each cell finishes, so an aborted
  matrix keeps every result it already paid for
- `report.json` — every run plus all three metric blocks
- `report.md` — the same numbers as markdown tables

Pass `--stamp` to name the run directory yourself; otherwise it is a UTC timestamp. An
existing run directory is never written into twice: the command exits 2 unless
`--overwrite` is passed, which clears the reports and this matrix's own run payloads
first. A confirmed run exits 1 if none of its runs produced comparable findings. The
only clock-derived report field is each run's `elapsed_seconds`; that and the recorded
cost are what differ between two otherwise identical runs.

## Adding labels

Add `expected_findings` to a corpus item to bring it into the efficacy table. All four
fields are required — `severity` has no default, because it is what the expected verdict
is derived from. `category` is part of the fingerprint, so it must be one a real review
emits: a `ReviewCategory` value (`logic-bug`, `silent-failure`, `integration`,
`test-gap`, `contract-drift`, `security`, `breaking-change`, `code-smell`,
`architecture`). A category no review reports can never match, and the label would
silently count as a permanent miss:

```yaml
- id: pr-1928
  pr: 1928
  expected_findings:
    - file: lintro/ai/review/orchestrator.py
      category: logic-bug
      title: Cost cap is checked after the request is issued
      severity: P1
```

Labels are adjudicated by a human. Competitor bot comments (archived by the #1962 pilot)
are candidates for adjudication, never ground truth.

## Tests

`tests/evals/` covers the metric functions on synthetic finding sets and the runner
against a mocked invoker. No test in that directory touches a provider or the network.

Those tests only run from a repository checkout: `evals/` is pruned from the wheel and
sdist, so an installed copy of lintro has no harness for them to import.

Two files spell the harness root literally and must be updated together if the directory
moves:

- `pyproject.toml` — `mypy_path = ["evals/review-efficacy"]`
- `tests/evals/conftest.py` — puts the root on `sys.path` before the tests import
  `review_matrix`

`evals/review-efficacy/run_matrix.py` also bootstraps `sys.path`, but it derives the
root from `__file__` and so needs no edit on a move.
