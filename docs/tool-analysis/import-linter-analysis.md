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

| Situation                               | Native `lint-imports`              |
| --------------------------------------- | ---------------------------------- |
| Contract broken                         | exit 1                             |
| Config present, contracts all kept      | exit 0                             |
| Config present, **zero** contracts      | exit 0                             |
| **No config file at all**               | error (non-zero)                   |
| Could not check, or crashed (see below) | exit 1, diagnostic, partial report |

## Lintro Implementation Analysis

### Preserved features

- The full contract set is read from the project's own native configuration. Lintro
  never synthesises or overrides contracts.
- Every built-in contract type (`layers`, `forbidden`, `independence`, `protected`,
  `acyclic_siblings`) is supported, because Lintro parses the reported import chains
  rather than modelling contract semantics itself. `acyclic_siblings` prints its
  dependencies relative to the package it checked (`- .b -> .a (1 import)` under
  `No cycles are allowed in pkg.`); Lintro resolves them to absolute chains
  (`pkg.b -> pkg.a`). A custom contract type whose report Lintro cannot read is still
  counted (see "Unreadable sections" below), and its text is shown.

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
- **A run without a complete report is an execution failure, not a finding.**
  `lint-imports` also exits 1 when it cannot check the contracts. Examples: an
  `ignore_imports` entry that matches no import under
  `unmatched_ignore_imports_alerting = "error"`, a root package that is not importable
  (`Could not find package 'x' in your Python path.`), a contract it rejects as
  misconfigured, or a crash. The check fails closed: a non-zero exit is a lint verdict
  only when all of these hold:
  - the exit status is exactly 1 (`EXIT_STATUS_ERROR` in `importlinter.cli`). Any other
    status, including a negative one from a signal, is an execution failure.
  - stdout has a `Contracts: N kept, M broken.` summary with `M` above zero;
  - stdout has the underlined `Broken contracts` heading;
  - at least `M` contract sections follow it, counted by heading, so two contracts that
    share a name count as two;
  - stdout has no Python traceback after the summary line, and stderr has no
    `Traceback (most recent call last):` block at all.

  The stderr rule is what catches a crash in the last contract's section. The report is
  printed from a call `lint-imports` does not guard, so an exception there leaves every
  heading in place, writes its traceback to stderr and exits 1, exactly like a verdict.
  Only a traceback-looking block on stdout _before_ the summary is tolerated, since the
  report that follows it completed. Status 1 alone never proves a verdict, because the
  tool also uses it for the errors it reports itself.

  Anything else is reported the way the black, ruff and clippy plugins report a failed
  run: `success` false, `execution_failed` true, `findings_complete` false, inspection
  coverage unknown. The issues are only the chains genuinely parsed before the run
  stopped, usually none. No issue is invented for the failure, so severity counts and
  SARIF stay truthful, and the unknown coverage keeps the run from updating a severity
  baseline. `ToolResult.output` starts `import-linter failed (exit N) running:`, then
  the command, then the tool's stdout and stderr (or
  `lint-imports produced no output.`). The console prints that block, below the issue
  table when chains were recovered. The Markdown report adds a "could not complete its
  check" note quoting it in a code block the text cannot close. Both keep the first 20
  and last 40 lines of a longer block, with a notice saying how many lines were omitted
  and that the full output is in the JSON report (`--output-format json`). JSON carries
  `"execution_failed": true` with the whole block in `output`.

- **Unreadable sections.** A structurally complete report stays a verdict even if one
  contract's section has a format Lintro cannot parse. Each such section is counted in
  `parse_failures_count`, inspection coverage is unknown (so the run cannot update a
  severity baseline), and the console prints the raw report beneath the chains that were
  parsed.

- **Other failure shapes are reported elsewhere.** A timeout is reported as a timed-out
  run with zero issues, not as an execution failure. A `pyproject.toml` that cannot be
  parsed never reaches `lint-imports`: config discovery skips it, so the run reports "No
  import-linter configuration found" unless another candidate file declares a section.
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
produces three issues. A run that could not check its contracts adds no issue of its
own; see the execution-failure entry under "Design decisions".

### Limitations

- **No fix mode.** Contract violations are design problems; there is nothing safe to
  rewrite automatically. `lintro format` never invokes import-linter.
- **Requires an importable root package.** grimp imports the package to build the graph,
  so the project's dependencies must be installed in the environment Lintro runs in.
- **`file` is a module path, not a file path.** A contract is broken by an import
  relationship, not by a source line, so the `file` field carries a dotted module name
  and `line` is `0`. Consumers that expect a filesystem path — SARIF artifact URIs,
  editor links, AI fix-path resolution — will not resolve these issues to a source file.

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
  there is one place to look — but note the precedence above: if the project already has
  a `setup.cfg` or `.importlinter` carrying an `[importlinter]` section, that file wins
  and contracts added to `pyproject.toml` are never read. Move the whole configuration
  rather than splitting it.
