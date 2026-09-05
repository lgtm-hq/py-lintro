# pylint Tool Analysis

## Overview

[pylint](https://github.com/pylint-dev/pylint) is a static analyser for Python with the
broadest check catalogue of any tool Lintro wraps. Most of that catalogue overlaps with
ruff, mypy and pydoclint — but one checker has no equivalent anywhere else in the
toolbox: `duplicate-code` (`R0801`), which finds blocks copy-pasted between modules.
That checker is the reason pylint is wrapped.

Both the PyPI distribution and the command-line entry point are named `pylint`.

## Core Tool Capabilities

pylint parses every module it is given into its own abstract syntax tree and runs each
enabled checker over the result. Messages carry a message id (`R0801`), a symbolic name
(`duplicate-code`) and a category:

| Category     | Prefix | Meaning                                  |
| ------------ | ------ | ---------------------------------------- |
| `fatal`      | `F`    | pylint could not process the module      |
| `error`      | `E`    | Probable bug                             |
| `warning`    | `W`    | Python-specific problem                  |
| `convention` | `C`    | Coding-standard violation                |
| `refactor`   | `R`    | Code smell — `duplicate-code` lives here |
| `info`       | `I`    | Informational                            |

Configuration lives in `pylintrc`, `pylintrc.toml`, `.pylintrc`, `.pylintrc.toml`,
`pyproject.toml` (`[tool.pylint.<section>]`), `setup.cfg` or `tox.ini`
(`[pylint.<section>]`), in that precedence order. Native exit status is a bit field, not
a simple success/failure:

| Bit | Value | Meaning              |
| --- | ----- | -------------------- |
| 0   | 1     | Fatal message issued |
| 1   | 2     | Error message issued |
| 2   | 4     | Warning issued       |
| 3   | 8     | Refactor issued      |
| 4   | 16    | Convention issued    |
| 5   | 32    | Usage error          |

A clean run exits 0; anything reported sets the matching bits (a run reporting only
`R0801` exits 8).

## Lintro Implementation Analysis

### Preserved features

- The full checker catalogue is driven by the project's own native configuration; Lintro
  synthesises no rule set of its own.
- The `json2` reporter is used verbatim, so message ids, symbols and categories are the
  ones pylint itself assigns.
- The `R0801` message body — the `==module:[start:end]` file list plus the duplicated
  source block — is kept **verbatim**. It is the only description of what is duplicated,
  and reformatting it would destroy the finding's content.

### Design decisions

- **Project-scoped, runs once.** `duplicate-code` only sees clones that appear inside a
  single invocation, so the plugin passes every discovered file to one `pylint` run
  rather than checking file by file. Per-file execution would report zero `R0801`
  findings on every codebase.
- **Config resolution walks upward.** The plugin walks up from each input path through
  every filename pylint reads, in pylint's own preference order, and passes the first
  real hit as `--rcfile`. Only the bare `pylintrc`/`.pylintrc` count on presence alone;
  the TOML files must declare a `tool.pylint` table (the document is parsed, not
  string-matched) and `setup.cfg`/`tox.ini` a `[pylint…]` section, so an ordinary
  `pyproject.toml` is never mistaken for pylint config.
- **No configuration is not a skip.** Unlike import-linter, pylint runs perfectly well
  with no config file, so the plugin runs it and lets pylint's defaults apply. Those
  defaults overlap heavily with ruff, which is why every project enabling pylint should
  configure it explicitly.
- **Exit status is not trusted on its own.** pylint's exit code is a bit field, so any
  non-zero status with a parseable JSON report is treated as "issues found". Only a
  non-zero exit with _no_ report — a usage error such as a missing rcfile (exit 32) — is
  an execution failure. The one exception is `No files to lint: exiting.`, which pylint
  prints with that same exit 32 when the effective configuration leaves nothing enabled;
  that is a clean pass. Output that is present but unreadable — invalid JSON, or JSON
  with no `messages` array — is reported as a parse failure, never as a clean pass.

### Issue mapping

| Lintro field | pylint `json2` field                                         |
| ------------ | ------------------------------------------------------------ |
| `file`       | `path`                                                       |
| `line`       | `line`                                                       |
| `column`     | `column`                                                     |
| `code`       | `messageId` (e.g. `R0801`)                                   |
| `symbol`     | `symbol` (e.g. `duplicate-code`)                             |
| `message`    | `message`, verbatim (multi-line for `R0801`)                 |
| `severity`   | Derived from `type` (see below); raw value in `message_type` |

Severity comes from pylint's category: `fatal` and `error` map to ERROR, `warning` and
`refactor` to WARNING, and `convention` and `info` to INFO. `duplicate-code` is a
`refactor`, so it surfaces as a WARNING rather than being buried in the informational
bucket.

One issue is emitted per pylint message. A clone set spanning three files produces one
`R0801` message, reported against the last file of the set — that is pylint's own
behaviour, not a Lintro choice.

### Limitations

- **No fix mode.** pylint reports and never rewrites. `lintro format` never invokes it.
- **Slow.** pylint walks a full AST per module. Use `jobs = 0` to fan the run out across
  all cores; the similarity checker implements pylint's map/reduce protocol, so `R0801`
  is still detected across workers.
- **Needs importable dependencies.** pylint's AST layer resolves imports, so third-party
  packages should be installed in the environment Lintro runs in, or pylint reports
  import errors.
- **`R0801` is reported once per clone set**, against one of the files involved. The
  other files appear only inside the message body, so file-based grouping shows the
  finding under a single file.

## Usage Comparison

| Native                                  | Lintro                                                                   |
| --------------------------------------- | ------------------------------------------------------------------------ |
| `pylint src`                            | `lintro check src`                                                       |
| `pylint --rcfile pyproject.toml src`    | `lintro check src` (config auto-discovered)                              |
| `pylint --disable=all --enable=R0801 .` | `lintro check . --tool-options "pylint:disable=all,pylint:enable=R0801"` |
| n/a                                     | `lintro check . --tools pylint`                                          |

## Configuration Strategy

pylint is fully native-config driven — see
[configuration.md](../configuration.md#pylint) for the option syntax. Lintro injects
nothing into the check set; `--tool-options pylint:disable=` / `pylint:enable=` are
forwarded straight to `--disable` / `--enable`.

## Recommendations

- Start from `disable = ["all"]` and enable only what no other tool covers.
  `duplicate-code` is the obvious first (and often only) entry.
- Tune `min-similarity-lines` to the smallest clone worth acting on; the default of 4 is
  far too noisy for most codebases. Turning on `ignore-comments`, `ignore-docstrings`
  and `ignore-imports` keeps the checker focused on real logic.
- Keep the configuration in `pyproject.toml` alongside the other tools — but note the
  precedence above: a `pylintrc` or `.pylintrc` in the same directory wins, and options
  added to `pyproject.toml` are then never read.
