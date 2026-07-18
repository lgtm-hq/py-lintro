# PHPStan Tool Analysis

## Overview

[PHPStan](https://phpstan.org/) is a static analysis tool for PHP that finds bugs in
code without running it. It infers types (even without annotations), validates function
and method signatures, and reports a wide range of correctness problems such as calls to
undefined functions, wrong argument counts, dead code, and null-safety violations. It is
a check-only tool: it reports issues but does not rewrite source files. This document
describes lintro's PHPStan integration and the design decisions behind it.

## Core Tool Capabilities

- **Type inference**: Reasons about types without requiring annotations.
- **Configurable strictness**: Analysis `level` from 0 (basic) to 9 (maximum).
- **Stable error identifiers**: Each finding carries an identifier (e.g.
  `arguments.count`, `function.notFound`) documented at
  `https://phpstan.org/error-identifiers/<identifier>`.
- **Remediation tips**: Many findings include a `tip` with a link or hint.
- **Framework extensions**: Optional extensions for Laravel, Symfony, Doctrine, etc.
- **Baseline support**: Legacy codebases can baseline existing errors.
- **Output formats**: `table`, `json`, `prettyJson`, `checkstyle`, `junit`, `github`,
  `gitlab`, `teamcity`, `raw`. PHPStan does **not** emit SARIF.

## Installation

PHPStan requires a PHP runtime.

```bash
# Per-project (recommended for real projects)
composer require --dev phpstan/phpstan

# System-wide (used by lintro's Docker image and Homebrew formula)
brew install php phpstan
```

## Usage in Lintro

```bash
# Analyze PHP files with PHPStan
uv run lintro check --tools phpstan path/to/src

# Override the analysis level (0-9)
uv run lintro check --tools phpstan --tool-options phpstan:level=6 path/to/src
```

### Analysis levels

| Level | Focus                                           |
| ----- | ----------------------------------------------- |
| 0     | Basic checks (unknown classes/functions, arity) |
| 5     | Type checking of arguments and return values    |
| 8     | Null-safety and stricter type rules             |
| 9     | Maximum strictness (`mixed` handling, etc.)     |

## Configuration and Level Decision

PHPStan requires an analysis `level`; unlike ruff or mypy there is no built-in "default"
level. Lintro therefore chooses **run-with-defaults**, not skip-on-no-config (contrast
stylelint/vale, which skip without config):

- When the project provides **no** native config (`phpstan.neon` / `phpstan.neon.dist` /
  `phpstan.dist.neon`), lintro injects `--level=0`. Level 0 is the most conservative
  setting: it reports only unambiguous problems (undefined symbols, wrong argument
  counts) that are real bugs and that do not depend on an autoloader or type
  annotations, which keeps false positives low on standalone files.
- When a native `phpstan.neon` **is** present, it defines the level, so lintro does
  **not** pass `--level` and defers entirely to the project configuration (mirroring how
  ruff/mypy respect their native config). Users who want stricter analysis add a
  `phpstan.neon` with their chosen `level:`.

This mirrors ruff/rubocop (run with sensible defaults) while still respecting native
configuration when it exists.

Standalone files without an autoloader are analyzed safely: PHPStan reports on them
without crashing (verified against bare `.php` files at level 0).

## Parser Choice: Native JSON (not shared SARIF)

Per the fidelity checklist in `docs/design/sarif-ingestion-evaluation.md`, lintro uses
the **native** PHPStan JSON parser (`--error-format=json`), not the shared SARIF
ingestion path, for two reasons:

1. **PHPStan does not emit SARIF.** Its formatters are
   `raw, gitlab, table, junit, checkstyle, teamcity, github, json, prettyJson`. There is
   no first-class SARIF output, so the shared SARIF parser is not even applicable
   without an external converter.
2. **SARIF would drop PHPStan-specific fidelity even if available.** PHPStan's JSON
   messages carry two fields lintro surfaces directly:
   - `identifier` (e.g. `function.notFound`) — drives the doc URL
     (`https://phpstan.org/error-identifiers/<identifier>`). Under SARIF this would
     depend on the tool populating `rules[].helpUri`, which PHPStan does not produce.
   - `tip` — a remediation hint that has no dedicated SARIF slot; it would be collapsed
     into a generic `properties` bag or lost.

The native parser preserves `identifier`, `tip`, `ignorable`, file, line, and message.

## Output Format Example

PHPStan `--error-format=json` output:

```json
{
  "totals": { "errors": 0, "file_errors": 2 },
  "files": {
    "src/App.php": {
      "errors": 2,
      "messages": [
        {
          "message": "Function add invoked with 1 parameter, 2 required.",
          "line": 5,
          "ignorable": true,
          "identifier": "arguments.count"
        },
        {
          "message": "Function nonExistentFunction not found.",
          "line": 6,
          "ignorable": true,
          "tip": "Learn more at https://phpstan.org/user-guide/discovering-symbols",
          "identifier": "function.notFound"
        }
      ]
    }
  },
  "errors": []
}
```

Lintro parses this into structured issues with file, line, code (identifier), message,
severity (always `ERROR` for PHPStan findings), and a documentation URL.

## Lintro Implementation Notes

- **Check-only**: `can_fix=False`; `fix()` raises `NotImplementedError`.
- **Tool type**: `LINTER | TYPE_CHECKER`.
- **File patterns**: `*.php`.
- **stdout/stderr separation**: PHPStan prints an AI-guidance preamble to stderr; the
  parser reads JSON from stdout only (via `_run_subprocess_result`), so stderr never
  corrupts parsing.
- **Exit codes**: PHPStan exits non-zero when it finds errors, so lintro relies on the
  parsed issue count rather than the process exit status to decide pass/fail.

## References

- <https://phpstan.org/>
- <https://phpstan.org/error-identifiers>
- <https://github.com/phpstan/phpstan>
