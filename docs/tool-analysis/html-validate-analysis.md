# html-validate Tool Analysis

## Overview

[html-validate](https://html-validate.org/) is an offline HTML validator that checks
HTML documents against standards, best practices, and accessibility (WCAG) rules. It
runs entirely locally with no network access. This analysis documents Lintro's wrapper
and the upstream `html-validate` behavior.

## Core Tool Capabilities

- HTML5 semantic validation (element nesting, required attributes, void elements)
- Accessibility (WCAG) checks (e.g., `wcag/h37` — images must have `alt` text)
- Detection of deprecated elements and attributes
- Document-structure and close-order validation
- Custom rule support and framework transforms (Vue, Angular, Svelte) via configuration
- Multiple output formatters: `json`, `checkstyle`, `codeframe`, `stylish`, `text`

## No-config behavior (confirmed)

When no `.htmlvalidate.*` configuration file is found, html-validate applies its
built-in **`html-validate:recommended`** preset. This means the tool produces useful
results out of the box with zero configuration — Lintro relies on this default. Verified
against html-validate 11.5.5:

```console
$ html-validate --formatter json index.html
[{"filePath":"index.html","messages":[
  {"ruleId":"wcag/h37","severity":2,
   "message":"<img> is missing required \"alt\" attribute",
   "line":5,"column":2,"selector":"html > body > img",
   "ruleUrl":"https://html-validate.org/rules/wcag/h37.html"}],
  "errorCount":1,"warningCount":0}]
```

A clean document emits `[]` and exits `0`; any error exits `1`.

## Installation

html-validate is a Node.js tool. Lintro installs and invokes it via `bun` (falling back
to `npx`, then a direct binary):

```bash
bun add -g html-validate        # or: npm install -g html-validate
```

The pinned version lives in `package.json` and is mirrored into
`lintro/tools/manifest.json` by the tool-version generator.

## Output format

html-validate emits an array of per-file results. Each message carries `ruleId`, numeric
`severity` (`2` = error, `1` = warning), `message`, `line`, `column`, `selector`, and
`ruleUrl`. Lintro parses this JSON directly (see parser choice below).

## Rule categories

- Document structure (e.g., `close-order`, `no-implicit-close`)
- Element usage and required attributes (e.g., `element-required-attributes`)
- Accessibility / WCAG (e.g., `wcag/h37`, `wcag/h30`)
- SEO and semantics
- Deprecated features
- Style / formatting (e.g., `void-style`, `attr-quotes`)

## Framework support

Under the default (no-config) preset html-validate parses the whole file as HTML. For
`*.vue` and `*.svelte` files this validates the template markup as raw HTML; proper
framework-aware transforms (isolating `<script>`/`<style>` blocks) require a project
`.htmlvalidate.json` that registers the relevant plugin/transform. Lintro registers
`*.html`, `*.htm`, `*.vue`, and `*.svelte` patterns; users wanting full framework
fidelity should add a native config, which html-validate discovers automatically.

## Parser choice: native JSON (not shared SARIF)

Per the SARIF ingestion fidelity checklist
(`docs/design/sarif-ingestion-evaluation.md`), the shared SARIF parser is only used when
it is lossless for the tool. **html-validate ships no SARIF formatter** (available
formatters are `json`, `checkstyle`, `codeframe`, `stylish`, `text`), so SARIF is not an
option at all. Lintro parses the native `--formatter json` output, which preserves every
field Lintro surfaces:

- rule id (`ruleId`) → `code`
- severity (`2`/`1`) → `error`/`warning`
- location (`line`, `column`)
- element context (`selector`)
- documentation link (`ruleUrl`) → `doc_url`

Had a SARIF formatter existed, converting through it would risk dropping the
tool-specific `selector` context and would require re-deriving severity and doc URLs;
the native parser avoids all of that.

## Lintro Implementation Analysis

### Preserved features

- Full validation via `html-validate --formatter json`
- Native config discovery (`.htmlvalidate.json`, `.htmlvalidate.js`, `.cjs`, `.mjs`)
  respected by the underlying tool
- File targeting for `*.html`, `*.htm`, `*.vue`, `*.svelte`
- Timeout control (default 30s) via `html_validate:timeout`
- Per-issue documentation URLs from the tool's own `ruleUrl`

### Limited / missing

- No auto-fix support (html-validate is a validator, not a formatter)
- No pass-through of advanced CLI flags beyond native config discovery
- Vue/Svelte validated as raw HTML unless a project config adds framework transforms

### Enhancements

- Centralized execution priority (default 30, alongside other linters)
- Unified issue formatting (rule, severity, selector, doc URL) shared with all tools
- stdout/stderr separation so stderr diagnostics never corrupt JSON parsing
