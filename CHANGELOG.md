<!-- markdownlint-disable MD024 -- duplicate headings are standard in changelogs -->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Deprecated

### Removed

### Fixed

### Security

## [0.80.6] - 2026-07-16

### Changed

- **tools**: rename manifest ToolRegistry to ManifestRegistry (#1260) (f729796)

- **tools/core**: rename `lintro.tools.core.tool_registry.ToolRegistry` to
  `ManifestRegistry` to disambiguate it from `lintro.plugins.registry.ToolRegistry`
  (#1220)

### Deprecated

- **tools/core**: `lintro.tools.core.tool_registry.ToolRegistry` is deprecated in favor
  of `ManifestRegistry`; importing or using the old name now emits a
  `DeprecationWarning` and will be removed in a future release (#1220)

### Fixed

- **ci**: use valid npm range syntax for astro allowedVersions (#1454) (e012d9d)

## [0.80.5] - 2026-07-16

### Changed

- **site**: add missing rehype devDependencies for doc-links suite (#1446) (140fd7c)

### Fixed

- **security**: pin astro to 7.0.9 to drop flagged 7.1.0 (MAL-2026-10726) (#1448)
  (491792f)

## [0.80.4] - 2026-07-16

### Changed

- **docker**: pin BuildKit syntax frontend by digest (#1384) (b183963)
- **deps**: update ghcr.io/lgtm-hq/lintro-tools:latest docker digest to 0024f54 (#1438)
  (7c191f7)
- **deps**: hold prettier >3.9.4 in Renovate (#1437) (7dc2c08)
- **deps**: update ghcr.io/lgtm-hq/lintro-tools:latest docker digest to fb89bff (#1435)
  (bf2e4bf)
- **deps**: update dependency rust-lang/rust to 1.97.1 (patch) (#1429) (f921f80)
- **deps**: hold TypeScript 7 and js-yaml 5 in Renovate (#1436) (c92445e)
- **deps**: migrate apps/site to Astro 7 and Vite 8 (#1428) (56181e0)

### Fixed

- **ai/review**: preserve Findings in section-aware _cap_body truncation (#1334)
  (f3d98f2)

## [0.80.3] - 2026-07-16

### Changed

- **deps**: ignore Renovate bumps for lintro npm placeholders (#1426) (f93c8d8)
- **deps**: update dependency astral-sh/uv to 0.11.29 (patch) (#1427) (25a7978)
- **deps**: update ubuntu:latest docker digest to 651ba3f (#1414) (cd97660)
- **deps**: update ghcr.io/lgtm-hq/lintro-tools:latest docker digest to 90978e9 (#1395)
  (f81d76d)
- **deps**: update all major dependencies (major) (#1402) (24faad8)
- **deps**: update rust toolchain to 1.97.0 (minor) (#1397) (9237163)
- **deps**: update dependency yaml to 2.9.0 (minor) (#1396) (8134abd)
- **deps**: update dependency google/osv-scanner to 2.4.0 (minor) (#1303) (9942f74)
- **deps**: update python:3.14-slim docker digest to d3400aa (#1393) (4a32469)
- **deps**: update ghcr.io/lgtm-hq/lintro-tools:latest docker digest to 45aa715 (#1392)
  (a04dcf5)
- **deps**: pin docker/dockerfile docker tag to 87999aa (#1391) (df821a1)
- **deps**: pin dependencies (#1254) (ce73c19)
- **deps**: update dependency rust-lang/rustfmt to 1.9.0 (minor) (#1310) (1882c7d)
- **deps**: update actions/cache to v6.1.0 (major) (major) (#916) (b94b0f9)
- **deps**: update github-actions (#1295) (0f1da6a)

### Fixed

- **tools**: pin TypeScript to 6.0.3 for Astro CLI verify (#1431) (ceb6ef1)

## [0.80.2] - 2026-07-14

### Changed

- **release**: dispatch Homebrew updates to the tap (#1380) (04ac4dc)
- **ci**: unify lgtm-ci pin at v0.54.0 (#1377) (5670fc7)

### Fixed

- **ci**: Renovate tool-pin bumps break generated-versions check (#1387) (510bd10)

## [0.80.1] - 2026-07-14

### Changed

- **ci**: invert docker-ci path filter to deny-by-default skip-list with drift test
  (#1373) (7aa0d36)

### Fixed

- **test**: bump osv clean sample to setuptools 83.0.0 (#1375) (3d482e3)

## [0.80.0] - 2026-07-14

### Added

- **docker**: build full image FROM published lintro-tools base (#1367) (294f252)
- **docker**: publish lintro-tools base image with digest-pinned FROM (#1364) (ef9e24b)

### Changed

- **ci**: skip heavy CI on auto version-bump PRs (#1371) (fb5676e)
- **ci**: changed-files dogfood lint on PRs with full-lint fallback and nightly full run
  (#1370) (703fa15)
- **ci**: promote docker images by digest instead of rebuilding in publish (#1368)
  (cd2e9fb)
- **ci**: add path filters to docker-ci and test-ci with always-green gates (#1363)
  (d9ee7cf)

### Fixed

- **ci**: allow timestamp.sigstore.dev egress in lintro-tools publish (#1366) (5c748cb)

## [0.79.4] - 2026-07-13

### Fixed

- **pytest**: honor zero coverage threshold and guard pyproject parsing (#1333)
  (7d61362)

## [0.79.3] - 2026-07-13

### Fixed

- **config**: address unresolved #1267 review threads (#1354) (dca09af)

## [0.79.2] - 2026-07-13

### Fixed

- **ci**: sync vuln-suppression workflow pin assertion (#1341) (c7208ac)
- **ci**: harden vuln suppression install against curl exit 23 (#1328) (0d9923d)

## [0.79.1] - 2026-07-13

### Changed

- **security**: verify ANTHROPIC_API_KEY exposure ordering for dogfood AI review (#1325)
  (c5caf17)

### Fixed

- **ci**: restore OpenSSF Scorecard webapp publish (#1331) (298fb34)

## [0.79.0] - 2026-07-13

### Added

- **config**: split ai.enabled into ai.lint / ai.review toggles (#1267) (d1ceac6)

### Changed

- **config**: reconcile configuration docs with runtime (#1275) (9f2c7d6)
- **config**: derive tool-section filter from tool registry (#1257) (2520c01)
- **node**: bump Node.js to 24 LTS everywhere it's pinned (#1271) (68c9ff3)
- **renovate**: drop package rules redundant with org preset (#1263) (707f758)

## [0.78.2] - 2026-07-12

### Changed

- **deps**: pin dependencies (#1293) (1980fbd)
- **docker**: allow deb.debian.org egress in docker publish builds (#1304) (8287eb7)

### Fixed

- **docker**: stop main-push overwriting multi-arch latest (#1323) (845531c)

## [0.78.1] - 2026-07-12

### Changed

- **deps**: update dependency setuptools to 83.0.0 (patch) (#1294) (c426508)

### Fixed

- **ci**: unblock main deploy-pages egress and dogfooding black finding (#1300)
  (70a9d48)

## [0.78.0] - 2026-07-12

### Added

- **tools**: add dotenv-linter for .env file validation (#1144) (d428037)

## [0.77.4] - 2026-07-12

### Fixed

- **test**: reconcile pytest addopts coverage with lintro banner (#1164) (a42b985)

## [0.77.3] - 2026-07-12

### Changed

- **deps**: pin dependencies (#1292) (4ecf180)
- **bench**: add comparative benchmark harness vs meta-linters (#1139) (52dfe8a)
- **ai**: externalize prompt templates to packaged files (#1134) (505894d)
- **deps**: update dependency fast-uri to 3.1.3 (patch) (#1291) (1aa1dbc)
- **deps**: update dependency defu to 6.1.7 (patch) (#1290) (7372683)

### Fixed

- **homebrew**: harden formulas and add brew audit/style CI (#1187) (7b65ca4)

## [0.77.2] - 2026-07-11

### Changed

- **ci**: adopt canonical emoji check names (#1283) (eb63aa7)
- **ci**: unify lgtm-ci pins at v0.52.3 (#1281) (2ef4016)

### Fixed

- **ci**: pass complete egress allowlist to release version-pr job (#1288) (623fb06)
- **ci**: grant permissions required by lgtm-ci v0.52.3 reusables (#1285) (d50428e)

## [0.77.1] - 2026-07-11

### Fixed

- **release**: gate publish jobs on anchored prerelease classifier (#1274) (47b2225)

## [0.77.0] - 2026-07-11

### Added

- **licenses**: add dependency license compliance checking (#1178) (83cf9a7)

## [0.76.0] - 2026-07-11

### Added

- **reporting**: add 0-100 health score to check runs (#1173) (83ff3ef)

## [0.75.0] - 2026-07-11

### Added

- **plugins**: support third-party tool plugins via Python entry points (#1137)
  (d659c93)

### Changed

- **config**: scope subprocess lint suppressions (B404/B603/B607) (#1135) (a2b597e)
- **ai/review**: externalize builtin checklist corpus to YAML (#1130) (9f9205c)

## [0.74.0] - 2026-07-10

### Added

- **tools**: add stylelint for CSS/SCSS/Less linting and fixing (#1143) (a81df54)
- **integration**: add consumer-facing pre-commit hook support (#1167) (3557eb6)

## [0.73.0] - 2026-07-10

### Added

- **ai/review**: machine-readable JSON error contract for provider failures (#1131)
  (c8d0aad)
- **tools**: add commitlint for conventional commit validation (#1147) (c6030df)

### Fixed

- **docker**: drop USER lintro so entrypoint can match volume owner (#1170) (8ebc9b2)
- **ci**: allow npm dist-tag for version backfills (#1208) (3fcf55c)

## [0.72.0] - 2026-07-10

### Added

- **tools**: add vale for prose/documentation linting (#1150) (9c1e1f9)
- **ai**: add idiom-review tool plugin (idiomatic-miss + duplication) (#1142) (33c7a27)

### Changed

- add ADRs and test-mapping guide for onboarding (#1166) (5f3c40d)
- **ai/review**: split monolithic chunker workflow module (#1132) (8f9cf8a)

### Fixed

- **mypy**: stop strict false-positives when project deps aren't installed (#1128)
  (fb74548)

## [0.71.3] - 2026-07-10

### Fixed

- **release**: format generated CHANGELOG and remove its .lintro-ignore entry (#1127)
  (f79349b)

## [0.71.2] - 2026-07-10

### Fixed

- **ci**: restore main Pages deploy after docs site merge (#1233) (c7d38d4)

## [0.71.1] - 2026-07-10

### Fixed

- **ci**: decouple lintro report from stale GHCR :latest pulls (#1032) (9904cd7)

## [0.71.0] - 2026-07-10

### Added

- **docs**: add documentation site and GitHub Pages deployment (#974) (3707809)

## [0.70.6] - 2026-07-10

### Changed

- **deps**: update digest (#917) (a69fbe5)
- **deps**: update rust-lang/rust to 1.96.1 (minor) (#915) (4c9825b)
- **deps**: update @astrojs/check to 0.9.9 (#866) (b346fc2)

### Fixed

- **astro-check**: run non-interactively to prevent prompt hang and timeout (#958)
  (0b1ea63)

## [0.70.5] - 2026-07-09

### Fixed

- **security**: update security policy and improve scorecard compliance (#787) (9eb520b)

## [0.70.4] - 2026-07-09

### Fixed

- **vue-tsc**: respect tsconfig.app.json preference in shared config discovery (#1125)
  (819e3e3)

## [0.70.3] - 2026-07-09

### Fixed

- **ci**: use Node 24 bundled npm for trusted publish (#1205) (d0543ab)

## [0.70.2] - 2026-07-09

### Fixed

- **homebrew**: sync binary formula generator with tap's authoritative output (#1199)
  (8dbd1ca)

## [0.70.1] - 2026-07-09

### Fixed

- **ci**: correct setup-node pin to real v6.4.0 SHA (#1201) (7ee16b1)

## [0.70.0] - 2026-07-08

### Added

- **npm**: publish via OIDC trusted publishing on release tags (#1194) (84b032e)

### Changed

- **deps**: update astral-sh/uv to 0.11.28 (#911) (63dfa6c)
- **deps**: update oven-sh/bun to 1.3.14 (#914) (936b078)
- **release**: version 0.69.6 (#1197) (1d42fa3)
- **docker**: bump lgtm-ci to v0.48.0 and add historical backfill dispatch (#1193)
  (1174f17)

### Fixed

- **npm**: scope meta-package as @lgtm-hq/lintro (#1182) (ca3a270)
- **ci**: always run CodeQL on PRs so its required check reports (#1196) (9d34024)

## [0.69.6] - 2026-07-08

### Changed

- **docker**: bump lgtm-ci to v0.48.0 and add historical backfill dispatch (#1193)
  (1174f17)

### Fixed

- **ci**: always run CodeQL on PRs so its required check reports (#1196) (9d34024)

## [0.69.5] - 2026-07-07

### Changed

- **ci**: bump lgtm-ci reusables to v0.47.1 (#1191) (7b34b01)

### Fixed

- **release**: surface release automation failures on main via run-name (#1133)
  (c964659)

## [0.69.4] - 2026-07-07

### Fixed

- **security**: remediate high-severity dependency vulnerabilities (#1126) (c674b5e)

## [0.69.3] - 2026-07-07

### Fixed

- **execution**: route all execution paths through per-execution isolated tool copies
  (#1124) (691bd54)

## [0.69.2] - 2026-07-07

### Fixed

- **output**: suppress 'run lintro fmt' hint in test mode (#1123) (94cd961)

## [0.69.1] - 2026-07-07

### Changed

- **contributing**: document merge-discipline and no-paper-over norms (#1121) (edba1ac)
- **build**: allow setup-uv endpoints in binary build egress policy (#1152) (969d8ac)

### Fixed

- **ai/review**: classify ValueError as INVALID_RESPONSE before shared severity
  signatures (#1122) (14b618f)

## [0.69.0] - 2026-07-06

### Added

- **install**: distribute lintro as npm package via platform binaries (#1141) (15078fb)

## [0.68.0] - 2026-07-06

### Added

- **shellcheck**: support source-following for repo-local includes (#1110) (3db899c)

## [0.67.0] - 2026-07-06

### Added

- **cli**: add fmt --dry-run mode to preview fixes without writing (#1109) (a53b686)

### Changed

- **sbom**: set fail-on-severity=high to stop 'negligible or higher' warnings (#1108)
  (30c715e)
- ignore auto-generated CHANGELOG.md + resync uv.lock to 0.66.0 (#1104) (6173f73)

### Fixed

- **ai/review**: prioritize non-diff-mappable findings before sticky-comment truncation
  (#1107) (8f343e1)
- **ci**: keep main green — revert SBOM hard-fail + decouple flaky Pages deploy (#1111)
  (2208ef3)
- **ai/review**: graceful partial when cost cap trips before any chunk (#1103) (d7d733c)

## [0.66.0] - 2026-07-06

### Added

- **ai/review**: post rich, telemetry-informative review comments (--post) (#1097)
  (2c9e3f6)
- **output**: show auto-fixable indicator in check output (#1093) (41c4d7a)

### Changed

- **changelog**: wrap release-note lines to satisfy lint gate (#1088) (1180000)
- **tools**: deduplicate tsc and vue_tsc definitions (76% identical) (#1092) (e13bb69)
- **tools**: replace repetitive tool-option type validation with schema-based checks
  (#1076) (caa0540)

### Fixed

- **ai/review**: provider-aware error taxonomy — surface real cause (not generic
  'aborted') (#1102) (08867ca)
- **ai/review**: exclude interpreter command-string operands from workflow script
  matching (#1090) (be07eeb)
- **mypy**: treat no Python files as a clean skip (#1089) (6164283)
- **osv_scanner**: treat malformed exit-0 payload as scan error, not clean (#1085)
  (e544d09)
- **plugins**: tool option mutation is not safe under parallel/thread execution (#1080)
  (6fe24b6)

## [0.65.0] - 2026-07-06

### Added

- **ci**: dogfood lintro review on py-lintro pull requests (#1072) (f2406a9)
- **ai**: wire review, summary, and fix to unified transport (#1023) (252d7e8)
- **ai**: unified AI transport foundation (#1022) (7a24fb3)
- **ai/review**: finding-centric checklist display (#1020) (9b99cef)
- **ai/review**: speed up review and add strictness tuning (#1019) (166d0b4)
- **ai/review**: add live progress bar for review operations (#1018) (e747d6f)
- **ai**: add Cursor provider via agent CLI (#1017) (35de0e7)
- **ai/review**: add --with-lint to feed tool results into review (#1038) (d0bea2b)
- **ai/review**: add GitHub PR review posting (#1037) (091df5d)
- **ai/review**: add terminal and JSON output formatters (#1036) (3761712)
- **cli**: add lintro review command (#1035) (b1b0b09)
- **ai/review**: add review orchestrator with depth and chunking (#1034) (45cbbd1)
- **ai/review**: add review prompt templates (#1011) (8e16733)
- **ai/review**: add checklist registry with file-glob triggers (#1003) (05f60c5)
- **ai/review**: add diff collection, classifier, and chunker (#1000) (4ae62b3)
- **ci**: migrate thin reusables to lgtm-ci v0.46.0 (#990) (b13f92d)

### Changed

- **site**: add honest comparison page vs trunk, MegaLinter, pre-commit, qlty (#1058)
  (9eab1b3)
- **config**: enforce module size limit via lint gate (#1078) (276929e)
- **output**: SARIF output should emit standard lint results, not only AI metadata
  (#1079) (4a423f1)
- **utils**: extract shared find_file_upward helper for duplicated config-walk logic
  (#1077) (8d983ca)
- pin merge_group activity type to checks_requested (#1059) (5f8edcd)
- **plugins**: separate stdout/stderr from subprocess and harden parsers (#1061)
  (23f6f09)
- **deps**: update actions/attest-build-provenance to v4.1.1 (#874) (d839d3d)
- **deps**: update python digest (#968) (eb2de3f)
- **deps**: update svelte to 5.56.4 (minor) (#969) (70d9001)

### Fixed

- **ai/review**: lintro review --pr crashes on invalid gh baseRepository field (#1084)
  (819040f)
- **ai/review**: guarantee secret redaction in git-native review mode (#1075) (a92fd93)
- **ci**: run dogfood review with trusted base-ref lintro, not PR code (#1074) (488c9b1)
- **ai/review**: correct patch line mapping and head-repo fallback (#1067) (6a07761)
- **ai/review**: harden review AI — secret redaction, severity normalization, response
  robustness (#1069) (7b0afe0)
- **ai**: Cursor provider cost accounting and opt-in --trust (#1068) (0a3af28)
- **output**: unify report counts and clean JSON stdout (#1060) (5111d6c)
- **ai/review**: CLI mode wiring broken — --uncommitted, --pr, and CI runs always fail
  (#1056) (4d0ccce)
- **ci**: unblock Renovate PRs blocked by mypy and artifact updates (#1057) (9159290)
- **deps**: update sqlfluff to 4.2.0 (minor) (#927) (efe3ea2)
- **ai/review**: Cursor review timeout reliability (#1021) (4cd3c7c)

## [0.64.5] - 2026-06-19

### Changed

- **ci**: bump lgtm-ci from v0.32.3 to v0.45.2 (#986) (36f047d)

### Fixed

- **ci**: grant missing permissions for lgtm-ci v0.45.2 reusable workflows (#988)
  (4636b7a)

## [0.64.4] - 2026-06-05

### Bug Fixes

- **renovate**: scope postUpgradeTasks to regex managers only (#970) (f196589)

### Other Changes

- **deps**: update lgtm-hq/lgtm-ci to v0.32.0 (minor) (#971) (90e6727)

## [0.64.3] - 2026-05-26

### Bug Fixes

- **ci**: repin coverage Pages publish to lgtm-ci v0.19.2 (#947) (88cd875)
- **ci**: annotate SHA pins and enable Renovate for lgtm-ci updates (#946) (9a54b4a)
- **ci**: repin lgtm-ci to v0.18.3 and fix egress allowlists (#943) (070e106)
- **ci**: merge coverage artifacts, add grype and Docker Hub egress (#942) (c6b4442)

### Other Changes

- **ci**: migrate to lgtm-ci v0.18.1 reusable workflows (#939) (7bfa7e7)

### Previously Unreleased

- **install**: Default `pip install lintro` is now lightweight (CLI only); bundled
  Python tools (ruff, black, mypy, bandit, pydoclint, yamllint) moved to `lintro[full]`
- **install**: Install profiles (`minimal`, `recommended`, `python`, `web`, `ci`,
  `full`) driven by `manifest.json`
- **install**: Interactive profile and tool selection in TTY mode; `--yes` / `-y` for
  non-interactive use
- **install**: `--write-lock` exports resolved plan to `.lintro-install.lock.json`
- **install**: Manual install bucket for tools whose package manager is unavailable
- **init**: `lintro init` detects project languages and generates `.lintro-config.yaml`;
  merges with existing config on rerun instead of clobbering
- **doctor**: Config-aware — respects `enabled_tools` and per-tool `enabled: false`;
  `--all` overrides; `--tools` takes explicit precedence
- **doctor**: `INCOMPATIBLE` status for versions below `min_version`; structured JSON
  output includes `min_version`, `recommended`, `disabled` counts
- **doctor**: `--fix` now also remediates `INCOMPATIBLE` tools
- **version**: `min_version` field in manifest for version tolerance; execution warns
  when installed version is below recommended but above minimum
- **parser**: `parse_failures_count` on `ToolResult`; surfaced in CLI and JSON output
- **onboarding**: First-run guidance when no tools are available; post-install next
  steps suggest `lintro init`, `lintro doctor`, `lintro check .`
- **homebrew**: Lightweight `lintro` formula (binary); `lintro-full` formula (PyPI with
  bundled tools)
- **tsc/vue-tsc**: Support TypeScript project references in monorepos — automatic
  sub-project discovery via `references` and directory walking (#803, #805)
- **tsc/vue-tsc**: Per-project framework detection in monorepos — Astro/Vue/Svelte
  detection is scoped per sub-project, not globally
- **tsc/vue-tsc**: "Deepest tsconfig wins" partitioning — overlapping parent/child
  configs no longer cause duplicate checking under conflicting compiler options
- **AI-Powered Features** (BYO API key, install with `'lintro[ai]'`):
  - AI-powered issue summaries with pattern analysis and prioritized actions
  - Interactive fix suggestions with AI-generated code diffs
  - AI-driven risk classification (`safe-style` vs `behavioral-risk`)
  - Multi-provider support: Anthropic Claude and OpenAI GPT
  - Post-fix summary contextualizing applied changes
  - Configurable retry, context lines, search radius, and timeout settings
  - Docker AI support via `WITH_AI` build arg
- **Plugin Architecture Migration**: Complete migration from tool-specific classes to
  unified plugin system
  - **API Changes**:
    - Old: `from lintro.tools.implementations.tool_ruff import RuffTool` and
      `RuffTool()`
    - New: `from lintro.plugins import ToolRegistry` and `ToolRegistry.get("ruff")`
    - Tool instances now expose `tool.definition.name` instead of `tool.name`
  - **Deleted Modules**:
    - All `lintro/tools/implementations/tool_*.py` files (12 files)
    - `lintro/tools/core/tool_base.py`
    - `lintro/models/core/tool.py` and `tool_config.py`
    - All `lintro/formatters/tools/*_formatter.py` files (13 files)
    - `lintro/tools/implementations/yamllint_config.py` and `yamllint_runner.py`
  - **New Plugin System**:
    - Tool definitions now in `lintro/tools/definitions/*.py`
    - Plugins use `lintro.plugins.BaseToolPlugin` base class
    - Unified formatter at `lintro/formatters/unified.py` replaces per-tool formatters
    - `ToolRegistry.get("tool_name")` to get tool instances
- **Python Version**: Lowered minimum Python version from 3.13 to 3.11
  - `pyproject.toml` updated with classifiers for 3.11, 3.12, 3.13
- **tsc/vue-tsc**: Respect tsconfig.json `include`/`exclude`/`files` scoping instead of
  overriding with all discovered files (#851)
- **Critical**: Fixed circular import bug in `lintro.parsers` module
  - Issue:
    `ImportError: cannot import name 'bandit' from partially initialized module 'lintro.parsers'`
    when running lintro as a dependency
  - Root causes:
    1. Eager imports in `parsers/__init__.py` causing circular dependencies
    2. Missing `lintro.parsers.bandit` package in setuptools configuration
  - Impact: Prevents lintro CLI from working when installed as a wheel distribution
  - Fix:
    1. Replaced eager imports with lazy loading via `__getattr__` in
       `lintro/parsers/__init__.py`
    2. Added `lintro.parsers.bandit` to setuptools packages list
  - Tests: Added comprehensive import tests for direct imports and lazy loading patterns
  - Verified: Works in both editable install (development) and built wheel (production)
- **PyPI Publication Workflow**: Fixed test failures in PyPI publish workflow by adding
  missing tool installation step
  - Added tool installation step (`./scripts/utils/install-tools.sh --local`) to PyPI
    workflow
  - Added PATH setup to ensure tools are available during test execution
  - Now matches the tool setup used in the main CI workflow
- **Tool Installation Script**: Improved compatibility with uv-based Python environments
  - Updated `install-tools.sh` to use `uv pip install` for Python packages when uv is
    available
  - Added detection for GitHub Actions environment and uv availability
  - Maintains fallback to pip for environments without uv
- **Package Distribution**: Fixed MANIFEST.in file patterns to eliminate build warnings
  - Updated Dockerfile pattern to match actual file names (`Dockerfile.*`)
  - Removed unnecessary `.rst` and `.txt` patterns for docs directory
  - Clean build process with no warnings during package creation
- **Files Modified**:
  - `.github/workflows/publish-pypi.yml` - Added tool installation and PATH setup
  - `scripts/utils/install-tools.sh` - Improved uv compatibility for Python package
    installation
  - `MANIFEST.in` - Fixed file inclusion patterns
- **Root Cause**: PyPI publish workflow was missing external tool dependencies (ruff,
  darglint, prettier, yamllint, hadolint) that integration tests require
- **Impact**: All tests now pass in PyPI publication workflow, enabling successful
  package distribution
- CI script path references for coverage comments
- Package metadata and classifiers
- Logo display in README for PyPI compatibility
- Initial release preparation
- PyPI package configuration
- MANIFEST.in file for asset inclusion
- CHANGELOG.md for version tracking

## [0.1.0] - 2024-07-26

### Added

- Initial release of Lintro
- Unified CLI interface for multiple code quality tools
- Support for Ruff, Darglint, Prettier, Yamllint, and Hadolint
- Multiple output formats (grid, JSON, HTML, CSV, Markdown)
- Auto-fixing capabilities where supported
- Docker support and containerized environments
- Comprehensive test suite with 85% coverage
- CI/CD integration with GitHub Actions
- Documentation and usage examples

### Features

- **Unified CLI**: Single command interface for all tools
- **Multi-language support**: Python, JavaScript, YAML, Docker
- **Rich output formatting**: Beautiful table views and multiple formats
- **Auto-fixing**: Automatic issue resolution where possible
- **Docker ready**: Containerized execution for consistency
- **CI/CD integration**: GitHub Actions workflows for automation

### Supported Tools

- **Ruff**: Python linting and formatting
- **Darglint**: Python docstring validation
- **Prettier**: JavaScript/TypeScript/JSON formatting
- **Yamllint**: YAML syntax and style checking
- **Hadolint**: Dockerfile best practices

### Technical Details

- Python 3.13+ compatibility (historical; minimum later lowered to 3.11)
- MIT License
- Comprehensive type hints
- Google-style docstrings
- Ruff and MyPy linting
- 85% test coverage
- Docker containerization
