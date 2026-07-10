# ADR-0005: uv for Python and bun for JavaScript tooling

## Status

Accepted

## Context

Lintro is a Python project that also wraps and installs JavaScript-based tools
(Prettier, oxlint, markdownlint-cli2). Both ecosystems need a fast, reproducible package
manager with a committed lockfile so that local, CI, and Docker environments resolve
identical dependency trees.

The repository commits to two toolchains:

- **Python:** managed with `uv`. `pyproject.toml` carries a `[tool.uv]` section and a
  `uv.lock` lockfile is committed. Contributor and CI commands invoke Python through
  `uv run` (see `docs/contributing.md` and `scripts/local/run-tests.sh`).
- **JavaScript:** managed with `bun`. `package.json` pins the JS tooling and a
  `bun.lock` lockfile is committed.

## Decision

Use `uv` as the Python package manager and runner, and `bun` as the JavaScript package
manager. Both lockfiles (`uv.lock`, `bun.lock`) are committed and are the source of
truth for dependency resolution.

## Consequences

- Fast, deterministic installs across local, CI, and Docker, with lockfiles guaranteeing
  reproducibility.
- Contributors need both `uv` and `bun` available; native `pip`/`npm` usage is avoided
  so the committed lockfiles stay authoritative.
- Dependency updates flow through the respective lockfiles; changes that bypass them
  (hand-edited installs) would drift from the reproducible baseline.

## References

- `pyproject.toml` (`[tool.uv]`) and `uv.lock`.
- `package.json` and `bun.lock`.
- `docs/contributing.md`, `scripts/local/run-tests.sh` — `uv run` usage.
