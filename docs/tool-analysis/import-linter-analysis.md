# import-linter Tool Analysis

## Overview

[import-linter](https://github.com/seddonym/import-linter) checks that a Python project
obeys a set of **import contracts** — rules about which parts of the codebase may import
which other parts. It is the only tool Lintro wraps that reasons about the _shape_ of a
package's import graph rather than about individual source lines.

The command-line entry point is `lint-imports`; the distribution on PyPI is
`import-linter`.

## Core Tool Capabilities

import-linter builds the import graph of a configured `root_package` (or
`root_packages`) with [grimp](https://github.com/seddonym/grimp) and evaluates every
declared contract against it. The built-in contract types are:

| Contract type      | What it enforces                                                 |
| ------------------ | ---------------------------------------------------------------- |
| `layers`           | Higher layers may import lower ones, never the reverse           |
| `forbidden`        | Named source modules must not import named forbidden modules     |
| `independence`     | A set of modules must not import one another in either direction |
| `protected`        | Only allowlisted modules may import the protected modules        |
| `acyclic_siblings` | Sibling modules under a parent must not form an import cycle     |

Contracts live in `pyproject.toml` (`[tool.importlinter]`), `.importlinter`, or
`setup.cfg` (`[importlinter]`). Native exit codes are:

| Situation                          | Native `lint-imports` |
| ---------------------------------- | --------------------- |
| Contract broken                    | exit 1                |
| Config present, contracts all kept | exit 0                |
| Config present, **zero** contracts | exit 0                |
| **No config file at all**          | error (non-zero)      |

## Lintro Implementation Analysis

### Preserved features

- The full contract set is read from the project's own native configuration. Lintro
  never synthesises or overrides contracts.
- Every contract type is supported, because Lintro parses the reported import chains
  rather than modelling contract semantics itself.

### Design decisions

- **Project-scoped, runs once.** import-linter analyses a whole package graph, so the
  plugin ignores the discovered file list and invokes `lint-imports` a single time per
  run. File discovery on `*.py` only decides _whether_ the tool is relevant for the
  paths given.
- **Config resolution walks upward.** The filename order — `setup.cfg`, `.importlinter`,
  then `pyproject.toml` — is the same order `lint-imports` uses. What Lintro adds is the
  _search_: it walks up from each input path rather than only looking in the process
  working directory, and runs the tool from the directory of the file it finds so the
  root package is importable. A candidate counts only when it really carries an
  import-linter section (the TOML is parsed, not string-matched), so a project's
  ordinary `pyproject.toml` is not mistaken for import-linter configuration.
- **No configuration found is a clean result, not a failure.** This is a deliberate
  divergence: the native tool errors when it can find no config file. Reporting clean
  lets import-linter stay enabled by default for projects that declare no contracts.
  This is a _different_ case from a config that exists with zero contracts — there the
  native tool already exits 0 and Lintro simply passes that through.
- **`--no-cache` is always passed** so a check never writes a cache directory into the
  project being linted, and `--no-logo` keeps the ASCII banner out of parsed output.

### Issue mapping

import-linter reports architectural violations, not line-level defects, so the Lintro
issue fields are mapped accordingly:

| Lintro field | Value                                                         |
| ------------ | ------------------------------------------------------------- |
| `file`       | Dotted path of the **importing** module that starts the chain |
| `line`       | Always `0` — a chain is not anchored to one line              |
| `code`       | Name of the broken contract                                   |
| `message`    | The import chain, e.g. `pkg.low -> pkg.a -> pkg.high`         |
| `severity`   | `ERROR` — a broken contract fails the run                     |

One issue is emitted per broken chain, so a contract broken by three distinct chains
produces three issues.

### Limitations

- **No fix mode.** Contract violations are design problems; there is nothing safe to
  rewrite automatically. `lintro format` never invokes import-linter.
- **Requires an importable root package.** grimp imports the package to build the graph,
  so the project's dependencies must be installed in the environment Lintro runs in.

## Usage Comparison

| Native                            | Lintro                                    |
| --------------------------------- | ----------------------------------------- |
| `lint-imports`                    | `lintro check .`                          |
| `lint-imports --config setup.cfg` | `lintro check .` (config auto-discovered) |
| n/a                               | `lintro check . --tools import-linter`    |

## Configuration Strategy

import-linter is fully native-config driven — see
[configuration.md](../configuration.md#import-linter) for the contract syntax. Lintro
injects nothing into the contract set.

## Recommendations

- Start with a single `layers` contract over the top-level packages and add contracts as
  the boundaries stabilise.
- Keep contracts in `pyproject.toml` alongside the rest of the tool configuration so
  there is one place to look.
