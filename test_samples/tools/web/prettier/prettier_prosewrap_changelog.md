# Changelog

## [0.52.4](https://github.com/example/repo/compare/v0.52.3...v0.52.4) (2026-02-24)

### Bug Fixes

* **ci:** publish semver-tagged Docker images on release ([#637](https://github.com/example/repo/issues/637)) ([850de62](https://github.com/example/repo/commit/850de6200000000000000000000000000000dead))
* **ci:** use full 40-char SHA for immutable Docker image tags to comply with SLSA provenance requirements and sigstore verification constraints ([#639](https://github.com/example/repo/issues/639)) ([1950030](https://github.com/example/repo/commit/195003000000000000000000000000000000dead))
* **ruff:** pass incremental and tool_name to file discovery so that incremental mode and tool-specific file patterns are respected during ruff check and fix operations ([#629](https://github.com/example/repo/issues/629)) ([83ac42d](https://github.com/example/repo/commit/83ac42d00000000000000000000000000000dead))
