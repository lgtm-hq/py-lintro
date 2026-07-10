# Vale Tool Analysis

## Overview

[Vale](https://vale.sh/) is a syntax-aware linter for prose and documentation. It checks
Markdown, reStructuredText, AsciiDoc, HTML, and plain text against configurable style
guides (Microsoft, Google, write-good, proselint, alex, and custom rules). This analysis
compares Lintro's wrapper with the upstream `vale` behavior.

> Vale requires a `.vale.ini` configuration to run. When no config is resolvable, Lintro
> skips vale as a non-error (rather than surfacing vale's hard `E100` runtime error),
> keeping `lintro check` clean on projects that do not use Vale.

## Core Tool Capabilities

- Prose linting for Markdown, reStructuredText, AsciiDoc, HTML, and plain text
- Configurable style guides via `BasedOnStyles` in `.vale.ini`
- Custom rule support authored in YAML
- Fast (written in Go) with editor integrations
- Machine-readable output via `--output=JSON`

## Built-in and Community Styles

- Microsoft Writing Style Guide
- Google Developer Documentation Style Guide
- write-good
- proselint
- alex (inclusive language)
- The bundled `Vale` style (ships with the binary; no `vale sync` required)

External styles are installed with `vale sync` after listing `Packages` in `.vale.ini`.

## Lintro Implementation Analysis

### ✅ Preserved Features

- Standard prose linting via `vale --output=JSON`
- Native config discovery respected (`.vale.ini`, `_vale.ini`, `vale.ini`); vale
  resolves configuration by walking up from each linted file
- File targeting for documentation patterns (`*.md`, `*.rst`, `*.adoc`, `*.txt`)
- Minimum alert level control via `vale:min_alert_level`
- Explicit config path via `vale:config`
- Timeout control (default 30s) via `vale:timeout`

### ⚠️ Limited / Missing

- **Config is required.** Vale cannot run without a resolvable configuration. When none
  is found, Lintro skips the tool as a non-error (with a helpful message) rather than
  surfacing vale's `E100` runtime error (exit 2).
- **No auto-fix.** Vale reports prose and style violations only; `fix()` raises
  `NotImplementedError`.
- No pass-through of advanced CLI flags (e.g. `--glob`, `--filter`, `--ignore-syntax`)
  beyond `--config` and `--minAlertLevel`.
- No custom formatter selection; the JSON output is used internally for parsing.

### 🚀 Enhancements

- Graceful skip when no config is present, keeping mixed-language runs clean
- Unified `ToolResult` with normalized issues from `vale_parser`
- Per-issue documentation links are taken from vale's `Link` field when a style provides
  them
- Vale's `suggestion` alert level is normalized to Lintro's `INFO` severity
- Safe version check with a skip result when vale is missing or below the required
  version

## Usage Comparison

```bash
# Core vale
vale --output=JSON docs/

# Lintro wrapper
lintro check docs/ --tools vale
lintro check docs/ --tools vale --tool-options vale:min_alert_level=warning
lintro check docs/ --tools vale --tool-options vale:config=.vale.ini
lintro check docs/ --tools vale --tool-options vale:timeout=60
```

## Output Format

Vale emits a JSON object keyed by file path; each value is a list of alerts:

```json
{
  "docs/example.md": [
    {
      "Action": { "Name": "edit", "Params": ["truncate", " "] },
      "Span": [1, 7],
      "Check": "Vale.Repetition",
      "Description": "",
      "Link": "",
      "Message": "'the' is repeated!",
      "Severity": "error",
      "Match": "The the",
      "Line": 3
    }
  ]
}
```

Lintro's parser maps `Check` to the display code (with the leading `<Style>` recorded
separately), `Span[0]` to the column, `Severity` to the normalized severity, and `Link`
to the documentation URL when present.

## Configuration Strategy

- Prefers native configs: `.vale.ini`, `_vale.ini`, or `vale.ini`.
- A minimal self-contained config uses only the bundled `Vale` style:

  ```ini
  MinAlertLevel = suggestion

  [*.md]
  BasedOnStyles = Vale
  ```

- Richer setups add `BasedOnStyles = Microsoft, Google` and run `vale sync` to download
  the referenced style packages.

## Comparison with markdownlint

- **markdownlint** checks Markdown _syntax_ and formatting (headings, list spacing, line
  length).
- **vale** checks _prose_ quality and style (repetition, passive voice, jargon,
  inclusive language).

The two are complementary and can run together on the same documentation set.
