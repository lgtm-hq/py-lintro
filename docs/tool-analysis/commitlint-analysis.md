# Commitlint Tool Analysis

## Overview

[commitlint](https://commitlint.js.org/) validates git commit messages against the
[Conventional Commits](https://www.conventionalcommits.org/) specification, enabling
automated changelog generation and semantic versioning. This analysis documents how
Lintro wraps the upstream `@commitlint/cli` binary.

## Core Tool Capabilities

- Validates commit-message format (`type(scope): subject`, body, footer)
- Ships with a shareable ruleset via `@commitlint/config-conventional`
- Highly configurable rules with two severity levels (`warning` = 1, `error` = 2)
- Reads input from a commit range (`--from`/`--to`), the last commit (`--last`), a
  message file (`--edit`), or stdin
- Requires a config file discovered via cosmiconfig
  (`commitlint.config.js`, `.commitlintrc.*`, or a `commitlint` key in `package.json`)

## Installation

```bash
bun add -g @commitlint/cli @commitlint/config-conventional
# or
npm install -g @commitlint/cli @commitlint/config-conventional
# or
brew install commitlint
```

A config is required. The minimal conventional setup:

```js
// commitlint.config.js
module.exports = { extends: ['@commitlint/config-conventional'] };
```

## Commit-Message Scope (Design Decision)

Unlike every other Lintro tool, commitlint does not inspect files — it inspects git
commit messages. To fit Lintro's file-oriented plugin model the plugin mirrors the
git-history-oriented `gitleaks` plugin:

- `file_patterns=["*"]` keeps shared execution preparation from short-circuiting when
  no tool-specific files are discovered. The discovered file list is then intentionally
  ignored.
- `check()` runs `commitlint --last`, validating the repository's most recent commit
  message in the working directory.

`--last` was chosen as the least-surprising default because it is deterministic (no
`HEAD~1` edge case on a single-commit repo, no reliance on remote refs such as
`origin/main`) and works in any git repository. commitlint cannot rewrite commit
messages, so the plugin is check-only (`can_fix=False`); `fix()` raises
`NotImplementedError`.

When no commitlint config is present, the plugin skips the tool as a non-error rather
than failing the run (it detects commitlint's exit code `9` and the "Please add rules"
message).

## Output Format and Parsing

commitlint does **not** emit SARIF, and it has **no** built-in JSON formatter — its
`--format` flag loads an external formatter module that is not bundled. Lintro therefore
parses commitlint's default human-readable report:

```text
⧗   --- input ---
bad commit message
✖   subject may not be empty [subject-empty]
✖   type may not be empty [type-empty]

✖   found 2 problems, 0 warnings
```

The parser (`lintro/parsers/commitlint/commitlint_parser.py`):

- Splits the report into per-commit `--- input ---` blocks and captures each commit's
  subject line (surfaced in the `file` display column for context).
- Extracts each `✖`/`⚠` violation line into a `CommitlintIssue` with the rule name
  (`code`), severity `level` (`error`/`warning`), and message.
- Strips ANSI colour codes and ignores the trailing summary line.

## SARIF Fidelity Note

Per the checklist in `docs/design/sarif-ingestion-evaluation.md`, the shared SARIF
ingestion path is only appropriate for tools that emit SARIF natively and losslessly.
commitlint emits no SARIF at all, so the shared parser is inapplicable and a native
parser is required. There is nothing SARIF would "drop" because there is no SARIF source
to ingest.

## Common Rules

| Rule                  | Level   | Description                                   |
| --------------------- | ------- | --------------------------------------------- |
| `type-empty`          | error   | Commit type must not be empty                 |
| `subject-empty`       | error   | Commit subject must not be empty              |
| `type-enum`           | error   | Type must be one of the allowed values        |
| `header-max-length`   | error   | Header must not exceed the configured length  |
| `body-leading-blank`  | warning | Body must be preceded by a blank line         |
| `body-max-line-length`| warning | Body lines must not exceed the configured max |

## Integration with Git Hooks

commitlint is commonly wired into a `commit-msg` hook (e.g. via husky) to validate
messages as they are written:

```bash
commitlint --edit "$1"
```

Lintro's plugin instead validates the already-committed `--last` message, which suits
CI and ad-hoc `lintro check` runs.

## References

- <https://commitlint.js.org/>
- <https://www.conventionalcommits.org/>
- <https://github.com/conventional-changelog/commitlint>
