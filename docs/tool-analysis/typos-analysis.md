# Typos Tool Analysis

## Overview

[typos](https://github.com/crate-ci/typos) is a source-code spell checker written in
Rust. It is designed to find and correct misspellings in code and documentation with a
very low false-positive rate — it understands programming conventions (identifiers,
escape sequences, hex literals) so it rarely flags legitimate tokens. This analysis
compares Lintro's wrapper implementation with the core typos tool and documents the
parser-selection decision.

## Core Tool Capabilities

- **Fast, broad scanning**: checks all text files in a tree; binary files are detected
  and skipped automatically.
- **Auto-fix**: `typos --write-changes` applies corrections in place.
- **Low false positives**: a curated dictionary keyed on common misspellings rather than
  a full natural-language dictionary.
- **Configurable**: project config via `typos.toml`, `.typos.toml`, or `_typos.toml`
  (custom dictionaries through `[default.extend-words]`, file scope through
  `[files] extend-exclude`).
- **Output formats**: `long` (default, human), `brief`, `silent`, `json`, and `sarif`.

## Lintro Implementation

- **Definition**: `lintro/tools/definitions/typos.py` — `can_fix=True`,
  `tool_type=ToolType.LINTER`, `file_patterns=["*"]`, native configs `typos.toml` /
  `.typos.toml` / `_typos.toml`.
- **Check**: runs `typos --format json <files>` and parses the newline-delimited JSON.
- **Fix**: detects issues, runs `typos --write-changes <files>`, then re-checks to
  report the `initial = fixed + remaining` breakdown expected by Lintro's fix pipeline.
- **Parser**: `lintro/parsers/typos/` (`parse_typos_output`, `TyposIssue`). Each finding
  captures the file, line, a 1-based column derived from the reported byte offset, the
  misspelled word, and its suggested corrections. The composed message has the form
  `"<typo>" should be "<correction>"`.

### JSON output shape

`typos --format json` emits one object per finding:

```json
{
  "type": "typo",
  "path": "README.md",
  "line_num": 3,
  "byte_offset": 18,
  "typo": "<misspelled>",
  "corrections": ["<suggested>"]
}
```

Only `type == "typo"` records are turned into issues; other diagnostic object types are
ignored.

## Parser choice: native JSON vs shared SARIF

typos can emit SARIF (`--format sarif`), so per the SARIF ingestion evaluation
(`docs/design/sarif-ingestion-evaluation.md`, Refs #1066) we assessed whether the shared
SARIF parser would be lossless here. It is **not**, so Lintro uses a **native JSON
parser**. What SARIF would drop for typos:

- **The structured `typo` / `corrections` fields.** These are the entire point of a
  spell checker. In SARIF they are not first-class: the word and its fix are only
  reachable by reverse-parsing the human-readable `message.markdown` / the
  `fixes[].artifactChanges[].replacements[].insertedContent.text`. The native JSON hands
  them over directly as `typo` and `corrections`.
- **The message.** typos populates `message.markdown` only — it emits **no**
  `message.text`. The shared SARIF parser reads `message.text`, so it would yield an
  empty message for every finding.
- **Rule identity / doc URLs.** typos has no rule IDs and emits no `rules[]` array, so
  the SARIF path contributes no `code` or `doc_url` — nothing gained over native JSON.

SARIF's only extra for typos is a richer fix region (start/end line and column), which
Lintro does not consume because it re-runs the tool in fix mode rather than applying
parsed replacements. Given the enrichment loss on the fields that matter most, the
native JSON parser is the higher-fidelity choice.

## Configuration in this repository

typos runs as part of Lintro's default tool set, so this repo ships a `.typos.toml` (the
project's spell-checker config, analogous to `.hadolint.yaml` and `.yamllint`). It
declares a small set of intentional project vocabulary and excludes a few test fixtures
that deliberately embed non-English or scrambled text. Every entry is documented inline;
it is a curated dictionary, not a generated suppression baseline.

## Installation

```bash
cargo install typos-cli    # from crates.io
brew install typos-cli      # Homebrew
```

Lintro's `scripts/utils/install-tools.sh` installs it automatically (pre-built binary
via cargo-quickinstall, falling back to `cargo install typos-cli`).
