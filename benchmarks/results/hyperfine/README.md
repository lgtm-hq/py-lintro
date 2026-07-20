# Hyperfine CLI overhead baselines

Committed JSON files here are **reference baselines** for
[#598](https://github.com/lgtm-hq/py-lintro/issues/598). They are machine-specific —
compare **relative** overhead on the same host, not absolute milliseconds across
machines.

| File                        | Comparison                                         |
| --------------------------- | -------------------------------------------------- |
| `ruff-check-overhead.json`  | `lintro chk --tools ruff` vs `ruff check`          |
| `mypy-overhead.json`        | `lintro chk --tools mypy` vs `mypy`                |
| `ruff-format-overhead.json` | `lintro fmt --tools ruff` vs `ruff format --check` |
| `multi-tool-overhead.json`  | `lintro chk --tools ruff,mypy` vs sequential tools |
| `baseline-meta.json`        | Host / git / hyperfine metadata for the run        |

Regenerate (overwrites these files):

```bash
make bench
# or
./benchmarks/run-hyperfine.sh
```

Local scratch exports (optional) can use a `.local.json` suffix; those are gitignored.
