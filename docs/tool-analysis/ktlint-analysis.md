# ktlint Tool Analysis

## Overview

[ktlint](https://pinterest.github.io/ktlint/) is an anti-bikeshedding Kotlin linter with
a built-in formatter. It enforces the official
[Kotlin coding conventions](https://kotlinlang.org/docs/coding-conventions.html) and the
Android Kotlin Style Guide with minimal configuration, eliminating style debates. ktlint
targets Kotlin (`.kt`) and Kotlin Script (`.kts`) files, honors `.editorconfig`, and can
auto-correct most violations. This analysis documents Lintro's wrapper implementation
and the parser-format decision.

ktlint is distributed as a self-executing launcher that **requires a JVM (Java 8+)** at
runtime.

## Core Tool Capabilities

- **Linting**: Enforces the official Kotlin conventions with a `standard` ruleset (plus
  an opt-in `experimental` ruleset).
- **Formatting**: `--format` (`-F`) auto-corrects most violations in place.
- **Kotlin Script**: Lints and formats `.kts` in addition to `.kt`.
- **EditorConfig**: Reads `.editorconfig` on the path to each scanned file; no
  ktlint-specific config file is required.
- **Code styles**: `--code-style=(android_studio|intellij_idea|ktlint_official)` selects
  the ruleset flavor (default `ktlint_official`).
- **Reporters**: `plain` (default), `plain-summary`, `json`, `sarif`, `checkstyle`, and
  `html`.

## Lintro Implementation

### Tool definition

- Registered via `@register_tool` as `KtlintPlugin` (`BaseToolPlugin`).
- `tool_type = LINTER | FORMATTER`, `can_fix=True`.
- `file_patterns = ["*.kt", "*.kts"]`, `native_configs = [".editorconfig"]`.
- `version_command = ["ktlint", "--version"]`.

### Command execution

Because ktlint carries a heavy JVM startup cost, Lintro invokes it **once per run over
the full batch of files** rather than once per file:

```text
# check
ktlint --reporter=json --log-level=error [--code-style=X] [--editorconfig=P] <files>

# fix (applied in place)
ktlint --reporter=json --log-level=error --format [...] <files>
```

`--log-level=error` suppresses ktlint's warn-level log line ("Lint has found errors than
can be autocorrected …") which is otherwise written to **stdout** ahead of the JSON
report. Lintro parses stdout independently of stderr (issue #1043); the parser
additionally stays robust to any stray leading log lines.

### Configuration behavior

ktlint runs with sensible defaults and needs no dedicated config file. It reads
`.editorconfig` from the directory tree of each scanned file, so project style (e.g.
`max_line_length`, `ktlint_code_style`, disabled rules via
`ktlint_standard_<rule> = disabled`) is picked up automatically. Lintro exposes two
pass-through options: `code_style` and `editorconfig` (a path to a default
`.editorconfig`).

### Fix semantics and the fix invariant

ktlint's JSON reporter does **not** expose per-issue auto-correctability. Some
`standard` rules (notably `standard:filename`, which requires the file name to match its
top-level declaration) cannot be auto-corrected. Lintro therefore derives the fix
accounting by measurement rather than by trusting a flag:

1. Run check → count initial issues.
2. Run `--format` → auto-correct in place.
3. Re-check → count remaining issues.

`fixed = initial − remaining`, preserving Lintro's `initial = fixed + remaining`
invariant. A file whose name does not match its class keeps a `standard:filename`
finding after formatting; a well-named file with only spacing issues is fully corrected.

## Output format

`ktlint --reporter=json` groups findings by file:

```json
[
  {
    "file": "src/Example.kt",
    "errors": [
      {
        "line": 2,
        "column": 15,
        "message": "Unexpected spacing before \":\"",
        "rule": "standard:colon-spacing"
      }
    ]
  }
]
```

The Lintro parser flattens this into `KtlintIssue` records (`file`, `line`, `column`,
`message`, `rule`). ktlint reports every finding as an error, so `KtlintIssue` defaults
its severity to `ERROR`. Rule ids are namespaced by ruleset (`standard:` /
`experimental:`); `doc_url()` routes to the ruleset's documentation page.

## Parser format decision: native JSON vs. shared SARIF

Per the SARIF ingestion evaluation (`docs/design/sarif-ingestion-evaluation.md`, #1066 /
PR #1140), a tool should use the shared SARIF parser only when its SARIF is **lossless**
for Lintro's model. ktlint 1.8.0 emits **both** `--reporter=json` and
`--reporter=sarif`, so both were captured from the same run and compared on the fidelity
checklist.

| Field             | JSON reporter                                     | SARIF reporter                                                                                                 | Winner            |
| ----------------- | ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ----------------- |
| Rule id           | `rule` = `standard:filename`                      | `ruleId` = `standard:filename`                                                                                 | equal             |
| File path         | absolute path, used directly                      | relative URI under `%SRCROOT%` rooted at `$HOME` (e.g. `../../private/tmp/...`) requiring uriBaseId resolution | **JSON**          |
| Line / column     | `line`, `column`                                  | `region.startLine`, `region.startColumn`                                                                       | equal             |
| End range         | not provided                                      | not provided (region has start only)                                                                           | equal (both lack) |
| Severity          | not provided (ktlint findings are uniform errors) | `level = "error"` (uniform)                                                                                    | equal in practice |
| Message           | `message`                                         | `message.text`                                                                                                 | equal             |
| Fix metadata      | none per issue                                    | `fixes[]` absent; `rules[]` is **empty**                                                                       | equal (both lack) |
| Doc URL (helpUri) | synthesized from a ruleset template               | unavailable — `rules[]` empty, so no `helpUri`                                                                 | **JSON**          |

**Decision: native JSON parser.** ktlint's SARIF is not lossless-superior. It adds no
fidelity over JSON — no end ranges, an empty `rules[]` array (so no `helpUri`/doc URLs),
and a severity level that ktlint never varies — while being actively worse for file-path
resolution (relative to `%SRCROOT%` = the home directory). This mirrors the evaluation's
hadolint finding, where an empty `rules[]` drops doc URLs. A shared SARIF path for
ktlint would therefore lose doc-URL fidelity and complicate path handling for no gain.
The native JSON reporter is also what the issue's implementation prompt specifies.

## Preserved vs. limited features

### Preserved

- ✅ Linting and formatting for `.kt` and `.kts`.
- ✅ `.editorconfig`-driven configuration.
- ✅ Rule ids preserved verbatim (`standard:` / `experimental:`).
- ✅ Auto-fix via `--format` with an accurate fixed/remaining split.
- ✅ Code-style selection and default-`.editorconfig` override.

### Limited / not exposed

- ⚠️ Per-issue auto-correctability is not surfaced by ktlint's JSON reporter; Lintro
  determines it by re-checking after `--format`.
- ⚠️ Custom `--ruleset` JARs and third-party reporters are not exposed.
- ➖ SARIF ingestion intentionally not used (see decision above).

## Installation

- **Homebrew**: `brew install ktlint` (pulls a JRE if needed).
- **SDKMAN!**: `sdk install ktlint`.
- **Manual**: download the launcher from
  [GitHub releases](https://github.com/pinterest/ktlint/releases) and place it on
  `PATH`; requires Java 8+.

Lintro's `scripts/utils/install-tools.sh` downloads the ktlint launcher and warns when
no JVM is found on `PATH`.

## References

- ktlint documentation: <https://pinterest.github.io/ktlint/>
- ktlint rules: <https://pinterest.github.io/ktlint/latest/rules/standard/>
- Kotlin coding conventions: <https://kotlinlang.org/docs/coding-conventions.html>
