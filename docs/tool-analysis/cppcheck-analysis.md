# Cppcheck Tool Analysis

## Overview

[Cppcheck](https://cppcheck.sourceforge.io/) is a static analysis tool for C and C++
code. It focuses on detecting undefined behavior and dangerous coding constructs (buffer
overruns, uninitialized variables, memory leaks, null pointer dereferences, division by
zero) that compilers typically miss, and is designed around a low-false-positive
philosophy. It runs standalone on individual translation units and needs no build system
or project context.

This analysis documents Lintro's wrapper implementation and, in particular, the parser
choice (native XML vs. SARIF).

## Core Tool Capabilities

- **Bug detection**: undefined behavior, memory safety, resource leaks, dead code,
  integer/division issues.
- **Severity model**: six levels — `error`, `warning`, `style`, `performance`,
  `portability`, `information`. `error` checks always run; the advisory categories are
  opt-in via `--enable`.
- **CWE mapping**: most findings carry a CWE identifier.
- **Inconclusive analysis**: `--inconclusive` surfaces findings that cannot be fully
  confirmed, trading some false positives for coverage.
- **Output formats**: human-readable text with a customizable `--template`, structured
  XML (`--xml`, schema version 2), and — in recent versions (verified against upstream
  2.21.0) — SARIF (`--output-format=sarif`).
- **Exit codes**: `--error-exitcode=N` makes cppcheck exit with `N` when any enabled
  finding is reported; otherwise it exits `0`.

## Installation

- macOS: `brew install cppcheck`
- Debian/Ubuntu: `apt-get install cppcheck`
- From source: <https://github.com/danmar/cppcheck>

## Lintro Implementation

### Command

Cppcheck is check-only (no fixer). Lintro runs, per invocation on the discovered C/C++
files:

```text
cppcheck --xml --quiet --error-exitcode=1 \
  --enable=warning,style,performance,portability [--inconclusive] \
  [--std=<std>] [--inline-suppr] [--suppress=<spec> ...] <files>
```

- The **XML report is written to stderr**; `--quiet` leaves stdout empty. Lintro runs
  one cppcheck process over the whole discovered file list and parses the combined
  stdout+stderr the batch runner returns, so the report is picked up either way.
- `--error-exitcode=1` is set so a clean run exits `0` and a run with findings exits
  `1`. Issue counting is driven entirely by the parsed XML; the exit code is only used
  to detect execution failures (a non-zero exit with no parseable findings is treated as
  a failure and surfaced, i.e. the plugin fails closed rather than reporting a silent
  pass — appropriate for a partly security-focused tool).

### Default checks

`error`-severity checks always run. Lintro additionally enables
`warning,style,performance,portability` by default. `unusedFunction` and `information`
are intentionally excluded from the default: the former requires whole-program analysis
and is unsupported because lintro may shard the file list, and the latter is mostly
configuration noise (e.g. missing system includes). The rest is configurable via the
`enable` option.

### Options

| Option         | Type             | Purpose                                                 |
| -------------- | ---------------- | ------------------------------------------------------- |
| `enable`       | str              | Comma-separated check categories (`error` is implicit). |
| `inconclusive` | bool             | Report findings cppcheck cannot fully confirm.          |
| `std`          | str              | Language standard (e.g. `c11`, `c++17`).                |
| `inline_suppr` | bool             | Honor inline `// cppcheck-suppress` comments.           |
| `suppress`     | str \| list[str] | Suppression specifications (e.g. `missingInclude`).     |

## Parser choice: native XML (not SARIF)

Recent cppcheck (upstream 2.21.0 was used for this evaluation) can emit SARIF
(`--output-format=sarif`), so the SARIF fidelity checklist from
`docs/design/sarif-ingestion-evaluation.md` was applied. **SARIF is lossy for
cppcheck**, so Lintro uses a native XML parser (`lintro/parsers/cppcheck/`):

- **Severity collapse (primary reason)**: SARIF has only
  `error`/`warning`/`note`/`none`. Cppcheck's `style`, `performance`, and `portability`
  findings all map to SARIF `warning`, erasing the distinction between them. The XML
  `severity` attribute preserves all six native levels. Lintro keeps the native string
  on `CppcheckIssue.severity` and normalizes to its tri-level display scale only at
  render time (advisory levels → `INFO`), so no information is lost in the model.
- **Inconclusive flag dropped**: the XML `inconclusive="true"` attribute has no SARIF
  representation; the native parser keeps it.
- **CWE representation**: the XML `cwe` attribute is a clean integer; SARIF encodes it
  indirectly as a `tags` entry (`external/cwe/cwe-NNN`).

The XML schema (version 2) is stable across cppcheck releases, so a native parser is not
materially more fragile than consuming SARIF here.

## Parsing details

- Each `<error>` becomes one `CppcheckIssue` with `code` (the check id), `severity`,
  `message`, `cwe`, and `inconclusive`.
- For value-flow traces with multiple `<location>` elements, the **first** location is
  the primary error site and is the one reported; later locations are reasoning steps.
- Meta diagnostics without a `<location>` fall back to the `file0` attribute and line
  `0`.

## Notes and limitations

- No auto-fix: cppcheck only reports.
- Cppcheck is declared `partitionable`, so lintro may run it over a subset of the tree.
  Each source file is its own translation unit, so that is safe for every default check.
  Whole-program checks are not: `unusedFunction` would report functions as unused merely
  because their callers sat in another shard, so it is excluded from the default enable
  set and enabling it explicitly is unsupported.
- Only source files are passed to cppcheck. A header handed to it directly is analyzed
  as a standalone translation unit, which misfires without the source that defines the
  macros and uses the declarations; upstream's manual says to pass sources instead.
  Headers are still covered — through the sources that `#include` them.
- Cppcheck ships no portable single binary, so the tools image and `install-tools.sh`
  install the distro package (apt on Debian, Homebrew on macOS). The version recorded in
  `lintro/_tool_versions.py` — and propagated to `lintro/tools/manifest.json` by the
  tool-version generator — therefore tracks the Debian release the `python:3.14-slim`
  base ships, currently trixie's **2.17.1**. It is deliberately not Renovate-managed:
  the manifest-vs-image gate requires the installed version to _equal_ the manifest
  version, so bumping the pin to an upstream tag apt cannot supply would fail CI
  permanently. Bump it when the base image moves to a new Debian release. The supported
  floor is the separate `min_version` of **2.13.0**.
