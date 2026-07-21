# TruffleHog Tool Analysis

## Overview

[TruffleHog](https://github.com/trufflesecurity/trufflehog) is a secrets scanner that
detects credentials — API keys, tokens, private keys, and 800+ other credential types —
using provider-specific regex detectors and entropy analysis. Its signature capability
is optional live **verification**: it can call the corresponding provider API to confirm
whether a detected credential is active.

This analysis describes Lintro's TruffleHog wrapper and how it complements the existing
`gitleaks` integration.

## Core Tool Capabilities

- **800+ detectors**: provider-specific patterns (GitHub, AWS, GCP, Slack, ...)
- **Verification**: `--no-verification` toggles live credential checking
- **Scan sources**: `filesystem`, `git`, `github`, `s3`, `gcs`, Docker images
- **Result filtering**: `--results verified,unverified,unknown`
- **Custom detectors**: `--config`
- **Path filtering**: `--include-paths` / `--exclude-paths`
- **Output**: `--json` (newline-delimited JSON), `--json-legacy`, GitHub Actions

## Lintro Implementation

Lintro runs TruffleHog in **`filesystem`** mode, which fits Lintro's file-oriented model
(git-history scanning is a separate concern from linting a working tree).

### Verification is disabled by default

Lintro passes `--no-verification` by default. Verification makes outbound network calls
to third-party providers to test candidate credentials. That is inappropriate for a
linter that must be:

- **Hermetic** — no network dependency for a local or CI lint run.
- **Deterministic** — results must not depend on provider availability or on whether a
  test/fake credential happens to resolve.
- **Safe** — never transmit repository contents to external services implicitly.

Verification can be explicitly re-enabled per run via
`--tool-options trufflehog:no_verification=False`, and the `verified` status is still
surfaced on every finding (it is simply `false` when verification is off).

### Output parsing

TruffleHog emits **newline-delimited JSON** (one object per line) on **stdout**, and
writes diagnostic logs to **stderr**. The native parser (`lintro/parsers/trufflehog/`)
reads stdout only (see #1043), skips diagnostic lines (objects without
`SourceMetadata`), and maps each finding to a `TrufflehogIssue` carrying the detector
name/type, verification status, decoder, source metadata (file + line), and detector
`ExtraData` (e.g. rotation guide).

### Safety hardening

TruffleHog exits `0` for clean scans, for findings (unless `--fail` is passed), **and
even when it cannot read a scan target** (it logs `encountered errors during scan` and
produces no findings). A secrets scanner must never report a clean pass from a scan that
did not run. The wrapper therefore:

- Resolves every requested path to an **absolute path** before scanning, because a
  relative path that does not resolve against TruffleHog's working directory yields a
  silent empty result.
- Treats non-empty-but-unparseable stdout as a **parse failure** (see #1044).
- Treats `encountered errors during scan` with empty stdout as a **failure**.

### Options

| Option            | Default | Flag                | Purpose                              |
| ----------------- | ------- | ------------------- | ------------------------------------ |
| `no_verification` | `True`  | `--no-verification` | Disable live credential verification |
| `results`         | `None`  | `--results`         | Filter output result types           |
| `config`          | `None`  | `--config`          | Custom detector configuration        |
| `exclude_paths`   | `None`  | `--exclude-paths`   | File of regexes for paths to exclude |
| `concurrency`     | `None`  | `--concurrency`     | Number of concurrent workers         |

## Why SARIF is not used

TruffleHog does **not** emit SARIF (its output formats are `--json`, `--json-legacy`,
and `--github-actions`), so the shared SARIF ingestion path
(`docs/design/sarif-ingestion-evaluation.md`) is not an option. Even if it did, SARIF's
result model has no natural home for TruffleHog's two most important security signals —
the **detector identity** and the **verification status** — which would have to be
squeezed into generic `properties`. A native parser keeps these fields first-class.
Lintro therefore uses a native parser.

## Relationship with gitleaks

TruffleHog and gitleaks are **complementary**, not redundant:

| Aspect       | gitleaks                            | trufflehog                                        |
| ------------ | ----------------------------------- | ------------------------------------------------- |
| Detection    | Configurable regex rules + entropy  | 800+ provider-specific detectors + entropy        |
| Verification | None                                | Live credential verification (disabled by lintro) |
| Config model | `.gitleaks.toml` rules              | Command-line + custom detector `--config`         |
| Output       | JSON array                          | Newline-delimited JSON (JSONL)                    |
| Best at      | Fast, tunable, CI-friendly baseline | Broad provider coverage, richer per-detector data |

They overlap on common credential shapes (e.g. GitHub PATs, AWS keys), but each catches
secrets the other can miss because their detection engines differ. Running both
maximizes coverage; each is an independent, no-fix `SECURITY` tool in Lintro.

## Usage

```bash
# Scan the current directory
lintro check --tools trufflehog .

# Filter to a specific path and raise concurrency
lintro check --tools trufflehog --tool-options trufflehog:concurrency=8 src/

# Explicitly enable live verification (makes network calls — off by default)
lintro check --tools trufflehog --tool-options trufflehog:no_verification=False .
```

## Installation

- **Homebrew**: `brew install trufflehog`
- **Binary**: download from the
  [releases page](https://github.com/trufflesecurity/trufflehog/releases)
- **Lintro**: bundled via `scripts/utils/install-tools.sh` (checksum-verified)

Run `lintro doctor` to confirm the detected version.
