# SARIF Ingestion as a Shared Parser Layer — Evaluation

> **Status:** Research spike (Refs #1066). This document evaluates whether a
> shared SARIF ingestion path could replace a subset of lintro's bespoke
> per-tool output parsers. It ships with a minimal proof-of-concept parser
> (`lintro/parsers/sarif/`) that is **not** wired into any tool plugin.
>
> **Recommendation:** _Partial adopt_ — see [Recommendation](#recommendation).

## Contents

- [Background](#background)
- [What SARIF is](#what-sarif-is)
- [Per-tool SARIF support matrix](#per-tool-sarif-support-matrix)
- [Model-mapping analysis](#model-mapping-analysis)
- [Proof of concept](#proof-of-concept)
- [LOC delete / add estimate](#loc-delete--add-estimate)
- [Risks](#risks)
- [Recommendation](#recommendation)
- [Proposed phasing](#proposed-phasing)

## Background

Each of lintro's ~27 integrated tools has a bespoke output parser under
`lintro/parsers/<tool>/`. When an upstream tool changes its output format on a
minor version bump, the corresponding parser breaks. Recent hardening work
(#1043 stdout/stderr separation, #1044 JSON parse-failure handling) exists
precisely because hand-rolled parsers are fragile against malformed or drifting
output.

SARIF (Static Analysis Results Interchange Format) is an OASIS-standard JSON
schema for static-analysis results. The hypothesis: for tools that emit SARIF
natively, a single well-tested SARIF parser is more resilient to upstream drift
than a per-tool regex/JSON parser, because the _schema_ is versioned and stable
even when a tool's human-readable or ad-hoc JSON output changes.

This is the **ingestion** direction (parsing upstream tool output). It is
distinct from #816, which concerns lintro's own SARIF **output** formatter.

## What SARIF is

SARIF 2.1.0 (OASIS, 2020) models results as:

```text
sarifLog
└── runs[]                         one run per tool invocation
    ├── tool.driver.name           tool name (e.g. "ruff")
    ├── tool.driver.rules[]         reportingDescriptor: id, helpUri,
    │                               shortDescription, defaultConfiguration.level
    └── results[]                   one entry per finding
        ├── ruleId / ruleIndex      reference into rules[]
        ├── level                   error | warning | note | none
        ├── message.text            human-readable message
        ├── locations[]             physicalLocation.artifactLocation.uri
        │                           + region.startLine/startColumn/endLine/endColumn
        └── fixes[]                 optional; artifactChanges + replacements
```

The relevant properties for lintro are a small, stable subset. The full schema
is large, but the fields lintro needs (file, line, column, message, rule id,
severity, fix presence, doc URL) live in well-defined, rarely-changing places.

## Per-tool SARIF support matrix

Support was verified by inspecting each tool's CLI in this environment where the
tool is installed, and against upstream documentation otherwise. "Native" means
the tool emits SARIF from a first-class flag with no external converter.

| Tool         | Kind             | SARIF native | Flag / mechanism                         | Notes |
| ------------ | ---------------- | ------------ | ---------------------------------------- | ----- |
| ruff         | Python linter    | ✅ Yes       | `--output-format sarif`                  | Verified 0.15.9. Rich `rules[]` with `helpUri`; `fixes[]` present. |
| semgrep      | Multi-lang SAST  | ✅ Yes       | `--sarif`                                | Verified 1.168. Relative URIs + `uriBaseId`. No `fixes[]`. |
| gitleaks     | Secret scanner   | ✅ Yes       | `--report-format sarif`                  | Verified: format list includes `sarif`. |
| hadolint     | Dockerfile lint  | ✅ Yes       | `-f sarif`                               | Verified 2.14. ⚠️ Emits **no** `rules[]` array → no `helpUri` → doc URLs lost. |
| osv-scanner  | Dependency vuln  | ✅ Yes       | `-f sarif`                               | Verified: format list includes `sarif`. |
| bandit       | Python security  | ⚠️ Plugin    | needs `bandit-sarif-formatter`           | Verified: built-in formats are csv/custom/html/json/screen/txt/xml/yaml — SARIF absent. |
| clippy       | Rust linter      | ⚠️ Converter | `clippy-sarif` (rustc JSON → SARIF)      | External tool in the chain. |
| mypy         | Python types     | ⚠️ Converter | third-party `mypy-sarif` / custom        | No native support. |
| shellcheck   | Shell linter     | ❌ No        | formats: checkstyle/diff/gcc/json/json1/quiet/tty | JSON → SARIF converter is trivial but is still a converter. |
| oxlint       | JS/TS linter     | ❌ No        | formats: checkstyle/default/github/gitlab/json/junit/stylish/unix | |
| actionlint   | GH Actions lint  | ❌ No        | `-format` Go template only               | Could template SARIF but not first-class. |
| yamllint     | YAML linter      | ❌ No        | parsable/standard/github/colored         | |
| sqlfluff     | SQL linter       | ❌ No        | human/json/yaml/github-annotation        | |
| markdownlint | Markdown linter  | ❌ No        | —                                        | |
| cargo-audit  | Rust deps        | ❌ No        | json                                     | |
| cargo-deny   | Rust deps        | ❌ No        | json/human                               | |
| tsc / vue-tsc / astro-check / svelte-check | Type checkers | ❌ No | compiler diagnostics | |
| pydoclint    | Docstring lint   | ❌ No        | —                                        | |
| pytest       | Test runner      | ➖ N/A       | not a static analyzer                    | Outcome model differs fundamentally. |
| black / prettier / rustfmt / oxfmt / shfmt / taplo | Formatters | ➖ N/A | diff/rewrite model | No finding stream to map. |

**Summary:** 5 tools emit SARIF natively (ruff, semgrep, gitleaks, hadolint,
osv-scanner). 3 more (bandit, clippy, mypy) are reachable via a plugin or
converter. The remaining linters have no native path; formatters and pytest are
out of scope by nature.

## Model-mapping analysis

lintro's internal model is `BaseIssue` (`lintro/parsers/base_issue.py`) with the
display contract produced by `to_display_row()`:

| lintro field | SARIF source                                              | Fidelity |
| ------------ | --------------------------------------------------------- | -------- |
| `file`       | `locations[0].physicalLocation.artifactLocation.uri`      | Clean. `file://` decoded to a path; relative URIs pass through. |
| `line`       | `region.startLine`                                        | Clean. |
| `column`     | `region.startColumn`                                      | Clean (SARIF is 1-based, matching lintro). |
| `end_line/col` | `region.endLine` / `region.endColumn`                   | Clean when present. |
| `message`    | `message.text`                                            | Clean. Loses SARIF `message.markdown` and argument substitution. |
| `code`       | `result.ruleId` (or `rules[ruleIndex].id`)               | Clean. |
| `severity`   | `result.level` → normalized via lintro's alias table      | Good; see below. |
| `fixable`    | `bool(result.fixes)`                                      | Coarse — boolean only; see [Risks](#risks). |
| `doc_url`    | `rules[ruleId].helpUri`                                   | Depends on the tool populating `rules[]`. |

### Severity mapping

SARIF defines four levels. lintro's `SeverityLevel` has three (ERROR, WARNING,
INFO). The existing alias table in `lintro/enums/severity_level.py` already maps
`error`/`warning`/`note`; the PoC adds one override (`none` → INFO) so no SARIF
level falls through to the default. When a result omits `level`, the PoC follows
the spec: fall back to the rule's `defaultConfiguration.level`, then `warning`.

This is a **fidelity gain** in one respect: today each parser hard-codes its own
severity strings (bandit LOW/MEDIUM/HIGH, semgrep ERROR/WARNING/INFO), and the
alias table already normalizes them. SARIF standardizes the vocabulary at the
source, so the shared path has less per-tool severity logic.

### Where fidelity is lost

- **Fix suggestions.** lintro's `RuffIssue` carries `fix_applicability`
  (`safe`/`unsafe`); SARIF's `fixes[]` has no equivalent of ruff's safety flag,
  so a SARIF-only path collapses "safe fix" and "unsafe fix" into a single
  `fixable=True`. The actual replacement text in `fixes[].artifactChanges` is
  richer than lintro records today, but lintro does not apply fixes from parsed
  output (it re-runs the tool in fix mode), so this is not consumed.
- **Per-tool enrichment.** `BanditIssue` composes a message from
  `test_id`/`test_name`/`confidence`; `SemgrepIssue` carries `cwe`/`category`.
  These live in SARIF under `result.properties` / `rule.properties`, but as
  tool-specific bags — a shared parser can pass them through generically but
  cannot reproduce each tool's bespoke message formatting without per-tool code,
  which defeats the point.
- **Doc URLs.** hadolint (verified) emits SARIF with **no** `rules[]` array, so
  `helpUri` is unavailable and `doc_url` comes back empty — whereas lintro's
  hadolint parser can synthesize a stable doc URL from a template. Doc-URL
  fidelity is therefore tool-dependent, not guaranteed by SARIF.

## Proof of concept

The PoC lives in `lintro/parsers/sarif/`:

- `sarif_issue.py` — `SarifIssue(BaseIssue)` adding `code`, `level`, `fixable`,
  `end_line`, `end_column`, `tool_name`, and a `DISPLAY_FIELD_MAP` that routes
  `code`←`code` and `severity`←`level` through the shared display contract.
- `sarif_parser.py` — `parse_sarif_output(str | None) -> list[SarifIssue]`.
  Flattens all runs, indexes each run's `rules[]` by id (with `ruleIndex`
  fallback), resolves `helpUri` and default severity, decodes URIs, and is
  defensive against malformed JSON, a non-object root, or a missing `runs`
  array (returns `[]` rather than raising).

It is validated in `tests/unit/parsers/test_sarif_parser.py` against **real**
SARIF logs generated in this environment from ruff and semgrep
(`tests/unit/parsers/sarif_fixtures/`), plus synthetic fixtures for severity
levels, `ruleIndex` resolution, `file://` decoding, location-free results,
multi-run logs, and malformed input. Observed behavior:

```text
ruff.sarif    → 3 issues: F401/F401/F841, all severity=ERROR, fixable=True,
                doc_url=https://docs.astral.sh/ruff/rules/...
semgrep.sarif → 1 issue:  dangerous-eval, severity=ERROR, fixable=False
```

The PoC is deliberately **not** registered with any tool plugin and does not
alter any existing parser.

## LOC delete / add estimate

Measured against the current tree (`wc -l`):

| Bucket                                                   | LOC       |
| -------------------------------------------------------- | --------- |
| **Added** — PoC shared SARIF parser + issue + `__init__` | **~324**  |
| Candidate deletion — ruff `ruff_parser.py` + `ruff_issue.py` | 240   |
| Candidate deletion — semgrep parser + issue              | 204       |
| Candidate deletion — gitleaks parser + issue             | 252       |
| Candidate deletion — hadolint parser                     | 69        |
| Candidate deletion — osv-scanner parser + issue          | 324       |
| **Gross candidate deletion (5 native tools)**            | **~1,089** |

**Caveats that shrink the realistic figure:**

- ruff SARIF covers `ruff check` only; the `ruff format` path (`ruff_format_issue`)
  is unaffected, so ruff's parser is only _partially_ retired.
- osv-scanner's `suppression_parser.py` (116 LOC, included above) parses lintro's
  own suppression config, not tool output — **not** replaceable by SARIF.
- Per-tool issue dataclasses encode display/enrichment fidelity (bandit message
  composition, semgrep CWE). Deleting them trades fidelity for uniformity.

Net effect for a partial migration of the 5 native tools: **add ~324 LOC of
shared, well-tested ingestion; retire on the order of 700–900 LOC of bespoke
parsing**, at the cost of the fidelity items above. The maintenance win is
concentration: one parser to harden against drift instead of five.

## Risks

| Risk | Severity | Detail / mitigation |
| ---- | -------- | ------------------- |
| **Fix-suggestion fidelity** | Medium | SARIF `fixes[]` is boolean-coarse for lintro; ruff's safe/unsafe distinction is lost. Mitigation: keep ruff's native parser for the fix path, or read `properties` for tool-specific flags (re-introduces per-tool code). |
| **Severity fidelity** | Low | 4→3 level collapse is well-defined and already handled by the alias table; `none`→INFO added. Low risk. |
| **Doc-URL fidelity** | Medium | Tool-dependent: hadolint emits no `rules[]` (verified), so `helpUri`/`doc_url` is empty. Mitigation: fall back to per-tool doc-URL templates when `rules[]` is absent. |
| **Enrichment loss** | Medium | Bandit/semgrep message composition and CWE/category live in tool-specific `properties`; a generic parser cannot reproduce bespoke formatting without per-tool code. |
| **Performance** | Low | SARIF logs are larger than terse JSON (ruff SARIF ≈ 10.9 KB for 3 findings vs a few hundred bytes of native JSON), and embed full rule descriptions. For large repos this is more IO/parse work, but still linear and JSON-parsed once. Measure before adopting on large scans. |
| **Tool availability of SARIF** | Medium | SARIF is opt-in per tool and version-gated (ruff gained it in 0.5). A migrated tool on an older pinned version silently lacks the flag — needs a capability probe and graceful fallback to the native parser. |
| **Two code paths during migration** | Low–Med | Native + SARIF parsers coexist per tool until cutover; more surface area transiently. |

## Recommendation

**Partial adopt.** Introduce the shared SARIF parser as an _optional, opt-in_
ingestion path for the subset of tools that emit SARIF natively **and** whose
SARIF is high-fidelity for lintro's model. Do **not** pursue a wholesale parser
rewrite, and do **not** force SARIF where a converter or plugin would add a new
runtime dependency.

Rationale:

- The mapping onto `BaseIssue` is clean for the core fields (file/line/column/
  message/code/severity), validated by the PoC against real ruff and semgrep
  output.
- The resilience argument holds for _schema-stable_ tools: one parser to harden
  beats five. But the win is real only where SARIF is well-populated — hadolint's
  empty `rules[]` shows the ceiling is tool-dependent.
- The fix-suggestion and enrichment fidelity losses mean SARIF should
  **augment**, not replace, parsers for tools where lintro leans on that detail
  (notably ruff's fix safety and bandit's confidence/CWE messaging).

Best initial candidates: **semgrep, gitleaks, osv-scanner** — security/finding
tools where SARIF is well-populated and lintro's enrichment is lightest. Treat
**ruff** as augment-only (keep native fix handling). Treat **hadolint** as
lower-value until `helpUri` is available.

## Proposed phasing

1. **Phase 0 — this spike.** Shared parser PoC + tests + this evaluation. No
   wiring. _(Done.)_
2. **Phase 1 — capability layer.** Add a per-tool "supports SARIF" probe
   (flag + minimum version) and a fallback contract: prefer SARIF when
   available, else the existing native parser. No behavior change yet.
3. **Phase 2 — pilot one tool.** Wire SARIF ingestion for **semgrep** behind the
   capability probe. Assert output parity (issue count, file/line, severity)
   against the native parser on a fixture corpus in CI before switching the
   default.
4. **Phase 3 — expand.** Roll out to gitleaks and osv-scanner with the same
   parity gate. Keep native parsers as the fallback path, not deleted.
5. **Phase 4 — evaluate deletion.** Only after parity has held across releases,
   consider retiring the superseded native parsers. Retain per-tool issue models
   where they carry enrichment lintro surfaces (ruff fix safety, bandit CWE).

Each phase is independently revertible and never removes a working parser before
its SARIF replacement has demonstrated parity.
