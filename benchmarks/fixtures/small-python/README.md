# small-python fixture

A minimal, self-contained Python-only project used as a stable benchmark target. The
files are intentionally clean (they pass ruff and mypy) so that timing reflects tool
startup and traversal cost rather than variable amounts of diagnostic output.

`pyproject.toml` disables the `module_size` gate (which defaults to enabled when its
table is absent) so hyperfine single-tool overhead runs are not inflated by a follow-up
pass (see `benchmarks/run-hyperfine.sh`).

Keep this fixture small and deterministic. Do not add files that depend on the network
or on tool-specific plugins.
