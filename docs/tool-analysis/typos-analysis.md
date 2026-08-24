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
  and skipped automatically when typos walks the tree itself. Paths named explicitly on
  the command line bypass both that binary detection and the configured excludes, so the
  Lintro wrapper compensates (see below).
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
- **Check**: runs `typos --format json --force-exclude <files>` and parses the
  newline-delimited JSON from stdout only, so a warning on stderr cannot corrupt the
  report.
- **Fix**: detects issues, runs
  `typos --format json --force-exclude --write-changes <files>` (the same always-on
  wrapper flags as check, so `[files] extend-exclude` still applies), then re-checks to
  report the `initial = fixed + remaining` breakdown expected by Lintro's fix pipeline.
- **Explicit-path safeguards**: Lintro always passes a resolved file list, and typos
  (like ripgrep) skips its ignore rules for paths given as arguments. `--force-exclude`
  restores the project's `[files] extend-exclude`, and the plugin additionally skips
  known binary suffixes and sniffs the first 8 KiB of remaining files for a NUL byte so
  `--write-changes` stays away from images, archives, and other binary assets. The sniff
  is a heuristic for suffix-less binaries, not a guarantee: unusual binary assets should
  still be listed in `[files] extend-exclude`.
- **ARG_MAX batching**: with `file_patterns=["*"]` a large tree would otherwise expand
  into a single argv that exceeds the OS limit and fails with `E2BIG`. Paths are split
  into budget-sized batches by `lintro/tools/core/argv_batching.py` (shared with
  TruffleHog) and the per-batch results are merged. A batch that exits non-zero
  _without_ a parseable report is tracked separately from the merged findings, so a
  genuine failure in one batch is never hidden by typos another batch reported. Two
  signals feed that: typos' own `{"type": "error", ...}` records on stdout (which can
  appear in the _same_ batch as real findings, e.g. one unreadable file among many), and
  a non-zero exit with nothing parseable at all.
- **Parser**: `lintro/parsers/typos/` (`parse_typos_report`, `TyposIssue`). Each finding
  captures the file, line, a 1-based column derived from the reported byte offset, the
  misspelled word, and its suggested corrections. `fixable` is set from whether typos
  offered any correction, and `column` is a 1-based **byte** index (typos reports byte
  offsets and the source line is not available to the parser), left at 0 when typos
  reported no usable offset. The composed message has the form
  `"<typo>" should be "<correction>"`, with several corrections comma-separated. When
  typos offers no corrections at all, the message instead reads `"<word>" is disallowed`
  (this happens for words banned through configuration rather than matched against the
  correction dictionary).

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

Only `type == "typo"` records become findings. `type == "error"` records are surfaced by
`_parse_typos_errors` and fail the run even when findings were also parsed. The plugin
calls `parse_typos_report` (the only public parser entry), which pairs both views of the
same stdout so check, fix, `--write-changes`, and the post-write re-check cannot consume
findings without also seeing diagnostics. The conventional `parse_<tool>_output` name is
intentionally not public: it would return an empty findings list for a diagnostic-only
stream and look like a clean scan. Informational types (`binary_file`, `file_type`) are
debug-logged and dropped. Any other record type is treated as a diagnostic so a future
typos release cannot vanish.

typos 1.49.0's `Message` enum also has `file` and `parse`, but the default spell-check
walker never emits them (`file` is `--files` listing; `parse` is the identifier/word
dump), so they stay off the allowlist.

A non-zero exit with no parseable findings also fails closed; typos' findings exit code
is 2, and any other non-zero is a runtime failure even if some JSON typos were emitted.

## When typos is selected

typos is language-agnostic, so it has no entry in the manifest's `language_map`. That
splits selection in two:

- On a **no-config first run**, tool selection comes from language detection
  (`_detection_scoped_tool_names` in `lintro/utils/execution/tool_configuration.py`),
  which does not select typos on its own. It enters through the "unmapped tool with a
  native config" branch — once a `typos.toml`, `.typos.toml` or `_typos.toml` exists at
  a scan root. crate-ci/typos also reads `[tool.typos]` in `pyproject.toml` and
  `[package.metadata.typos]` / `[workspace.metadata.typos]` in `Cargo.toml`; those
  filenames are intentionally omitted from `native_configs` so every Python or Rust
  project does not auto-enable the plugin. The binary still honors those tables once
  typos is selected. An empty `[tool.lintro]` table is not a config (the guard is
  `tools is None and config.config_path is None and not config.tools`), so this path
  still applies.
- With **any resolved lintro config** (`.lintro-config.yaml` / `.yml`, a **non-empty**
  `[tool.lintro]` table in `pyproject.toml`, or an in-memory `tools:` section), or under
  `--tools all`, language scoping is skipped. Typos then runs when the binary is on
  `PATH` **and** it is not filtered by `execution.enabled_tools` or
  `tools.typos.enabled: false`. `lintro init --profile recommended` writes a
  language-map `enabled_tools` allowlist that does not include typos. An unscoped config
  (`enabled_tools: []` or omitted) will start spell-checking on upgrade.

Explicit selection (`--tools typos`) works in either case.

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

This repository keeps an unscoped `enabled_tools: []` config, so typos runs on
`lintro check` here. That is not universal: no-config first runs and
`lintro init --profile recommended` do not select typos (see **When typos is
selected**). The repo ships a `.typos.toml` (the project's spell-checker config,
analogous to `.hadolint.yaml` and `.yamllint`). It declares a small set of intentional
project vocabulary and excludes a few test fixtures that deliberately embed non-English
or scrambled text. Every entry is documented inline; it is a curated dictionary, not a
generated suppression baseline.

## Installation

```bash
cargo install typos-cli    # from crates.io
brew install typos-cli      # Homebrew
```

Lintro's `scripts/utils/install-tools.sh` installs it automatically (pre-built binary
via cargo-quickinstall, falling back to `cargo install typos-cli`).
