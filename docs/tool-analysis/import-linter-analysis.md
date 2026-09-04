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
`setup.cfg` (`[importlinter]`). A broken contract exits with status 1; a clean run exits
0, including when no contracts are declared at all.

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
- **Config resolution walks upward.** The plugin looks for `setup.cfg`, `.importlinter`,
  then `pyproject.toml` from each input path upward, mirroring how `lint-imports` itself
  resolves configuration, and runs the tool from that file's directory so the root
  package is importable.
- **No configuration found is not a failure.** A project with no import contracts
  reports a clean result instead of an error, so import-linter can stay enabled by
  default.
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
