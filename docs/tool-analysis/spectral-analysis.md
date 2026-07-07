# Spectral Tool Analysis

## Overview

[Spectral](https://stoplight.io/open-source/spectral) is a flexible JSON/YAML
linter with first-class support for OpenAPI (2.0/3.0/3.1), AsyncAPI, and JSON
Schema documents. This analysis compares Lintro's wrapper with the core
Spectral CLI and documents the parser-choice decision.

## Core Tool Capabilities

- **API-document linting**: OpenAPI 2.0/3.0/3.1, AsyncAPI, and JSON Schema
- **Custom rulesets**: `.spectral.yaml`/`.yml`/`.json`/`.js`, extending built-in
  rulesets (`spectral:oas`, `spectral:asyncapi`)
- **Built-in best-practice rules**: operation descriptions, unique operation
  IDs, defined path parameters, example validation, and many more
- **Multiple formatters**: `json`, `stylish`, `sarif`, `junit`, `html`,
  `github-actions`, and others
- **Check-only**: Spectral has no autofixer

## Installation

Spectral is distributed as the npm package `@stoplight/spectral-cli`:

```bash
bun add -d @stoplight/spectral-cli   # project-local (lintro's convention)
npm install -g @stoplight/spectral-cli
```

The package installs a `spectral` binary. Lintro prefers a `spectral` binary on
`PATH`, falling back to `bunx @stoplight/spectral-cli` (or `npx`).

## Ruleset Requirement and File Targeting

Spectral **requires a ruleset** — without one it exits non-zero with
"No ruleset has been found." OpenAPI/AsyncAPI documents are ordinary YAML/JSON
files, so linting every YAML/JSON file in a repository would be noisy and wrong.

Lintro therefore **only runs Spectral when a ruleset is present**. The plugin
discovers a `.spectral.*` file upward from the target (mirroring Spectral's own
resolution) or uses an explicit `ruleset` option. When no ruleset is found, the
tool **skips gracefully as a non-error** (the same pattern lintro uses for other
ruleset-gated tools). This makes the file patterns (`*.yaml`, `*.yml`, `*.json`)
safe: they are inert until a project opts in with a ruleset.

## Parser Choice: Native JSON (not shared SARIF)

Spectral 6.16 emits **both** `--format json` and `--format sarif`, so this is a
real case for the fidelity checklist in
[`docs/design/sarif-ingestion-evaluation.md`](../design/sarif-ingestion-evaluation.md).
Both formats were captured from the **same run** (`spectral:oas` on a minimal
OpenAPI 3.0 spec, v6.16.1) and compared.

### SARIF vs native JSON — field-by-field

| Field | Native JSON | SARIF | Verdict |
| --- | --- | --- | --- |
| Rule code | `code` (`oas3-api-servers`) | `ruleId` | Tie |
| Message | `message` | `message.text` | Tie |
| Line / column | `range.start.line`/`character` (**0-based**) | `region.startLine`/`startColumn` (1-based) | Both lossless; JSON needs a `+1` shift |
| **JSON path** | `path: ["paths","/users","get"]` | **absent** | **JSON only** |
| Severity | integer `0..3` (error/warn/**info**/**hint**) | `level` (error/warning/**note**/**note**) | **JSON only** — SARIF collapses info **and** hint to `note` |
| Doc URL | absent (template) | `helpUri` in `tool.driver.rules[]` | SARIF only |
| Source file | `source` (absolute path) | relative `uri` | Tie |

### Where SARIF is lossy for Spectral

- **JSON pointer path is dropped.** Spectral's native JSON carries a `path`
  array pointing at the exact node in the API document
  (e.g. `paths./users.get`). This is Spectral's defining location signal for
  structured specs and the SARIF output omits it entirely.
- **Severity fidelity is reduced.** Spectral's four diagnostic levels
  (error=0, warn=1, info=2, hint=3) collapse in SARIF: both `info` and `hint`
  map to `note`, erasing a distinction the native JSON preserves.

### Where SARIF would help

- SARIF embeds `helpUri` per built-in rule in `tool.driver.rules[]`, whereas the
  native JSON has no per-finding doc URL. Lintro compensates with a single
  documentation-page template (rule codes are ruleset-defined, so per-code URLs
  are unreliable for custom rulesets anyway).

### Decision

**Native JSON parser.** Per the evaluation's rule — adopt the shared SARIF
parser only when it is lossless for the tool — Spectral's SARIF is **not**
lossless: it discards the JSON-pointer `path` and collapses `info`/`hint`
severity. The native JSON parser (`--format json`) retains both, converting the
zero-based offsets to lintro's one-based convention. The only SARIF advantage
(doc URLs) is recovered with a documentation-URL template.

## Lintro Implementation Analysis

### ✅ Preserved Features

- ✅ Runs `spectral lint --format json` and maps every finding to a structured
  issue (rule code, message, one-based line/column, JSON path, severity)
- ✅ Retains the JSON-pointer `path` that SARIF drops
- ✅ Discovers `.spectral.*` rulesets upward from the target, matching Spectral
- ✅ Skips gracefully (non-error) when no ruleset exists

### ⚠️ Defaults and Notes

- ⚠️ Check-only; `fix()` raises `NotImplementedError` (Spectral has no fixer)
- ⚠️ Severity is normalized to lintro's ERROR/WARNING/INFO (hint → INFO)
- ⚠️ Doc URLs use a single reference page because rule codes are ruleset-defined

### 🚀 Enhancements

- ✅ Normalized output (tables/JSON) via lintro formatters
- ✅ Unified CLI, version checking (`lintro doctor`), and reporting

## Usage Comparison

### Core Spectral

```bash
spectral lint --ruleset .spectral.yaml openapi.yaml
spectral lint --format json openapi.yaml
```

### Lintro Wrapper

```bash
lintro check --tools spectral openapi.yaml
```

```python
from lintro.tools.definitions.spectral import SpectralPlugin

tool = SpectralPlugin()
result = tool.check(["openapi.yaml"], {})
```

## Output Format Example

`spectral lint --format json` emits an array of findings:

```json
[
  {
    "code": "operation-operationId",
    "path": ["paths", "/users", "get"],
    "message": "Operation must have \"operationId\".",
    "severity": 1,
    "range": { "start": { "line": 6, "character": 8 } },
    "source": "/repo/openapi.yaml"
  }
]
```

Severity integers map as `0=error`, `1=warning`, `2=info`, `3=hint`; line and
character offsets are zero-based and converted to one-based in lintro.

## Built-in Rulesets

- **`spectral:oas`** — OpenAPI 2.0/3.0/3.1 best practices
- **`spectral:asyncapi`** — AsyncAPI best practices

A project enables Spectral by adding a ruleset that extends one of these:

```yaml
# .spectral.yaml
extends: ["spectral:oas"]
```

Custom rules can be layered on top; see the
[Spectral rulesets documentation](https://docs.stoplight.io/docs/spectral/).
