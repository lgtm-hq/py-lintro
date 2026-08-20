# Hyperfine CLI overhead results

`./benchmarks/run-hyperfine.sh` writes its JSON exports here for
[#598](https://github.com/lgtm-hq/py-lintro/issues/598).

**Nothing in this directory except this README is committed.** Hyperfine timings are
machine-specific (CPU, OS, thermal state, installed tool versions), and a checked-in
snapshot silently rots the moment the runner's commands change, so
`benchmarks/results/hyperfine/*.json` is gitignored. Generate your own on the machine
you care about and compare **relative** overhead there — hyperfine's
ratio-vs-`--reference` column — not absolute milliseconds across machines.

| Generated file              | Comparison                                                  |
| --------------------------- | ----------------------------------------------------------- |
| `ruff-check-overhead.json`  | `lintro chk --tools ruff` vs `ruff check`                   |
| `mypy-overhead.json`        | `lintro chk --tools mypy` vs `mypy`                         |
| `ruff-format-overhead.json` | `lintro fmt --tools ruff` vs `sequential-ruff-fmt.sh`       |
| `multi-tool-overhead.json`  | `lintro chk --tools ruff,mypy` vs `sequential-ruff-mypy.sh` |
| `baseline-meta.json`        | Host / git / hyperfine metadata plus the methodology notes  |

The two `sequential-*.sh` references under `benchmarks/hyperfine/` run their tools
unconditionally and return the worst exit status — they do not short-circuit like `&&`,
because lintro does not either.

Generate (overwrites leftover `*-overhead.json` in this directory, including
files from a previous `--suite` that is not part of the current invocation):

```bash
make bench
# or
./benchmarks/run-hyperfine.sh
# write elsewhere instead:
HYPERFINE_RESULTS_DIR=/tmp/bench ./benchmarks/run-hyperfine.sh --quick
```
