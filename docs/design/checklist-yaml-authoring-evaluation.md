# Checklist YAML Authoring — Discoverability & Validation Evaluation

> **Status:** Research spike (Refs #1309). This document evaluates authoring-time
> discoverability and validation options for the externalized built-in review checklist
> corpus (`lintro/ai/review/checklist/corpus/*.yaml`). It does **not** implement the
> feature; throwaway prototypes lived under `/tmp` only and were not committed.
>
> **Recommendation:** _Adopt generated JSON Schema + editor modelines, with a
> regenerate-and-diff drift guard_ — see [Recommendation](#recommendation).

## Contents

- [Background](#background)
- [Current state](#current-state)
- [Prototype findings](#prototype-findings)
- [Approach comparison](#approach-comparison)
- [Drift guard design](#drift-guard-design)
- [Scope call](#scope-call)
- [Recommendation](#recommendation)
- [Proposed file layout](#proposed-file-layout)
- [Contributor workflow](#contributor-workflow)
- [Implementation task list](#implementation-task-list)
- [Risks](#risks)

## Background

PR #1130 (issue #1031) moved the built-in review checklist from inline Python into
versioned YAML under `corpus/`. Runtime safety was preserved: the loader fail-fast
validates categories, domains, languages, id/question uniqueness, tier/id ranges, and
unknown fields at import.

What was lost is the **authoring-time** experience the enums provided. Editing YAML
today means typing bare strings from memory (or copying neighbors) and discovering
mistakes only when the loader rejects them on the next import/`pytest`. For a corpus
explicitly meant to be editable without Python fluency, that is a regression.

Any solution that lists valid `category` / `domains` / `languages` values introduces a
second representation of the vocabulary. **Drift between the Python enums and the
authoring aid is the central design problem** — a stale schema that completes wrong
values is worse than no schema.

## Current state

| Artifact | Role today |
| -------- | ---------- |
| `corpus/tier1.yaml`, `corpus/tier2.yaml` | Data SSOT for built-in checklist rows |
| `loader.py` | Parse + fail-fast validation at import |
| `ChecklistItem` (`models/checklist_item.py`) | **Frozen dataclass** (not Pydantic) |
| `ReviewCategory`, `FileDomain` | `HyphenatedStrEnum` vocabulary for category/domains |
| `identify.identify.ALL_TAGS` | Vocabulary for `languages` (~311 tags) |
| `constants.py` | Tier/id range constants (`1–15`, `≥100`, …) |
| `.yamllint` | **Ignores** `lintro/ai/review/checklist/corpus/` (long prose questions) |
| `ReviewChecklistItemConfig` (`config/review_config.py`) | **Pydantic** model for _user_ checklist items in `.lintro-config.yaml` — related vocabulary, different shape (no `id`/`tier`; requires at least one axis) |

Implications for schema generation:

- There is **no** `ChecklistItem.model_json_schema()` path today.
- Pydantic is already a first-class dependency and already emits correct enums for
  `ReviewCategory` / `FileDomain` when a throwaway model is built around them.
- Converting `ChecklistItem` to Pydantic solely to get a schema is unnecessary churn;
  a small generator that reads the enums (and optionally a thin Pydantic
  `TypeAdapter`) is enough.

## Prototype findings

Throwaway work under `/tmp/checklist-schema-spike/` (not committed) exercised the three
investigation axes.

### a. Generated JSON Schema + `# yaml-language-server: $schema=...`

- A ~9 KB Draft 2020-12 schema was generated from `ReviewCategory`, `FileDomain`, and
  `identify.ALL_TAGS`, with `additionalProperties: false`, required fields, and
  `allOf`/`if`/`then` constraints for Tier 1 empty axes + id ranges and Tier 2 id
  minimums.
- Both corpus files validated cleanly against that schema via `check-jsonschema`
  (`uvx --from check-jsonschema`, v0.37.4). A deliberate bad `category: nope` failed
  as expected.
- A parallel Pydantic `TypeAdapter(list[CorpusRow])` (throwaway model) produced a
  usable `json_schema()` with `$defs` for the two enums. It did **not** enum
  `languages` unless the generator injected `ALL_TAGS` — so a dedicated generator is
  preferable to raw `model_json_schema()` alone.
- **yamllint conflict:** none observed. Adding either
  `# yaml-language-server: $schema=./checklist-corpus.schema.json` or the IntelliJ
  form `# $schema: ./…` to sample files passed both the repo `.yamllint` config and
  `extends: default` (line-length disabled for the sample). The corpus directory is
  already ignored by the repo config, so the directive is belt-and-suspenders for
  local/ad-hoc yamllint runs.
- **Relative paths:** Red Hat YAML Language Server and JetBrains resolve modeline
  relative paths from the **YAML file's directory**, not the workspace root. A schema
  sibling to `corpus/` therefore needs `../corpus.schema.json` from inside
  `corpus/*.yaml`. Prefer dual modelines so VS Code/Cursor and JetBrains both associate
  without settings:

  ```yaml
  # yaml-language-server: $schema=../corpus.schema.json
  # $schema: ../corpus.schema.json
  ```

### b. CI-side validation via `check-jsonschema` or `lintro chk`

| Mechanism | Result |
| --------- | ------ |
| `check-jsonschema --schemafile … corpus/*.yaml` | Works today via `uvx`; catches enum/shape errors. Not a repo dependency. |
| `lintro chk` / yamllint | Style only; **no** schema validation. Corpus path is ignored. Extending the yamllint plugin into a schema checker would be a new product surface. |
| Existing loader + unit tests | Already fail CI on invalid corpus at import. Do **not** replace; keep as defense in depth. |
| `jsonschema` Python package | Present in the lockfile as a transitive dependency, **not** a direct importable dep of the default env. Prefer not to lean on it without an explicit dependency decision. |

Conclusion: editor schema + a pytest/regenerate drift guard cover the acceptance
criteria without adding `check-jsonschema` as a hard CI dependency. Optional later:
a thin `uvx check-jsonschema` step for belt-and-suspenders.

### c. Alternatives noted

| Alternative | Verdict |
| ----------- | ------- |
| Convert `ChecklistItem` → Pydantic for `model_json_schema()` | Rejected for v1. Larger model migration than needed; generator can reuse enum types without changing the runtime dataclass. |
| Re-author corpus in Python | Undoes #1130's non-Python-fluent editing goal. |
| Spectral / custom YAML linter | Extra toolchain; JSON Schema already has mainstream editor + CLI support. |
| Docs-only enum tables | Useful as a supplement; alone fails completion/inline-validation acceptance. |
| Schema for full `.lintro-config.yaml` via `LintroConfig.model_json_schema()` | Valuable later, out of scope for this spike (see [Scope call](#scope-call)). |

## Approach comparison

| Approach | Editor completion / inline errors | Works without local Python | Drift risk | CI cost | Fits repo today |
| -------- | --------------------------------- | -------------------------- | ---------- | ------- | --------------- |
| **A. Generated JSON Schema + modelines** (committed artifact) | ✅ VS Code/Cursor (Red Hat YAML) + JetBrains via dual modeline | ✅ schema in git | Low **if** regenerate-and-diff is mandatory | Low (pytest) | ✅ mirrors `generate-tool-versions.py --check` |
| **B. `check-jsonschema` in CI only** | ❌ no editor UX | N/A for authors | Low if schema generated | Medium (`uvx` or new dep) | Partial — CI only |
| **C. Wire schema check into `lintro chk`** | ❌ unless combined with A | N/A | Low if generated | Medium–high (new tool surface) | Overkill for a single internal corpus |
| **D. Docs reference tables only** | ❌ | ✅ | Medium (hand-updated docs) | None | Weak alone |
| **E. Hand-maintained schema** | ✅ | ✅ | **High — rejected by issue** | Low | ❌ |
| **A + loader tests** (recommended combo) | ✅ | ✅ | Mechanically guarded | Low | ✅ |

## Drift guard design

**Single source of truth:** Python enums (`ReviewCategory`, `FileDomain`) +
`identify.ALL_TAGS` + `constants.py` tier/id bounds. The JSON Schema file is a
**generated artifact**, never hand-edited.

Follow the existing pattern in `scripts/ci/generate-tool-versions.py`:

1. **Generator script** (stdlib + project imports via `uv run`) that writes
   `corpus.schema.json`.
2. **`--check` mode:** regenerate to a temp buffer, unified-diff against the committed
   file, exit non-zero on drift.
3. **Unit test:** invoke the generator's check/diff path (or assert in-memory schema
   enum sets `==` `{m.value for m in ReviewCategory}` / `FileDomain` / `ALL_TAGS`) so
   `pytest` fails if someone edits enums without regenerating.

Reject:

- Hand-maintained schema PRs.
- “Docs say regenerate” without a mechanical gate.
- Relying on editor association alone (editors are optional; CI is not).

**What the schema will not (and need not) duplicate:**

- Cross-row unique `id` / unique normalized `question` — JSON Schema cannot project
  uniqueness across array objects cleanly; keep these in `loader._validate_corpus`.
- Empty-question-after-strip edge cases already covered by the loader.

Defense in depth: schema (authoring + optional CI) **and** loader (runtime) both stay.

## Scope call

**Checklist corpus only for the implementation PR.**

| Surface | Decision |
| ------- | -------- |
| `corpus/tier1.yaml`, `corpus/tier2.yaml` | **In scope** — primary pain from #1130 |
| `.lintro-config.yaml` / `LintroConfig` | **Out of scope for v1.** Already Pydantic-validated at load; authoring schema is a larger nested product surface (tools/defaults/ai/review/…). Revisit as a follow-up that can call `LintroConfig.model_json_schema()` (or a curated subset for `review.checklist.items`) using the same generator/`--check` pattern. |
| Future corpora | Use the same generator pattern when they appear; do not generalize prematurely. |

## Recommendation

**Adopt approach A (+ existing loader tests): commit a generated JSON Schema beside the
corpus, associate it with dual editor modelines, and enforce drift with a
regenerate-and-diff script + unit test.**

Rationale:

1. Restores completion and inline validation for `category`, `domains`, and
   `languages` in mainstream editors without requiring a Python env to _edit_.
2. Keeps enums as the only vocabulary SSOT; the schema is a build artifact with a
   mechanical gate (same operational model as tool-version generation).
3. Leaves `ChecklistItem` as a dataclass and leaves `lintro chk` / yamllint unchanged.
4. Does not replace loader fail-fast validation.
5. Avoids new runtime dependencies (`check-jsonschema` remains optional/`uvx` if ever
   desired).

Do **not** convert `ChecklistItem` to Pydantic for this. Do **not** hand-maintain the
schema. Do **not** expand to full `.lintro-config.yaml` schema in the first
implementation PR.

## Proposed file layout

```text
lintro/ai/review/checklist/
├── corpus/
│   ├── tier1.yaml              # + dual $schema modelines (relative ../)
│   └── tier2.yaml              # + dual $schema modelines
├── corpus.schema.json          # GENERATED — do not hand-edit
├── loader.py                   # unchanged validation semantics
└── __init__.py

scripts/
└── generate-checklist-corpus-schema.py   # write | --check (diff)

tests/unit/ai/review/
└── test_checklist_corpus_schema.py       # drift guard + maybe schema validates corpus
```

Notes:

- Place the schema **beside** `corpus/`, not inside it: `.yamllint` ignores the whole
  `corpus/` tree; a sibling `.json` is never yamllint-scanned anyway, and the relative
  modeline path stays a stable `../corpus.schema.json`.
- Do **not** add `corpus.schema.json` to setuptools `package-data` unless a future
  consumer needs it at runtime — authoring is a contributor/repo concern.
- Optional later: `.vscode/settings.json` `yaml.schemas` glob association as a
  convenience; modelines alone satisfy the “open the file and it works” bar.

### Generator responsibilities (sketch)

Emit Draft 2020-12 schema with:

- `type: array` of objects; `additionalProperties: false`
- `category` / `domains[]` enums from `ReviewCategory` / `FileDomain`
- `languages[]` enum from sorted `identify.ALL_TAGS`
- `tier` ∈ `{1,2}`; Tier 1/2 `if`/`then` for id ranges and Tier 1 empty axes
- Header comment in the JSON `description` field: “Generated from …; do not hand-edit”

Stable key ordering (sorted enums, sorted object keys) so diffs stay reviewable.

## Contributor workflow

How an author learns the schema exists:

1. **In-file modelines** at the top of each corpus YAML (discoverable on open in a YAML
   language server–enabled editor).
2. Short note in the corpus file header comment: “Valid `category` / `domains` /
   `languages` values come from the generated schema; regenerate with
   `uv run python scripts/generate-checklist-corpus-schema.py` after enum changes.”
3. One paragraph in contributor docs (checklist / AI review section) pointing at the
   generator and the drift test.
4. When an enum changes, CI/pytest fails with a clear “schema out of date; regenerate”
   message from `--check`.

Authors without the Red Hat YAML / JetBrains JSON Schema integration still get CI
protection via the loader + (once implemented) schema validation test.

## Implementation task list

Follow-up implementation PR (after maintainer sign-off on this spike):

1. Add `scripts/generate-checklist-corpus-schema.py` with default write + `--check`
   (unified diff, exit codes aligned with `generate-tool-versions.py`).
2. Commit initial `lintro/ai/review/checklist/corpus.schema.json`.
3. Add dual modelines to `corpus/tier1.yaml` and `corpus/tier2.yaml`; extend the short
   file headers.
4. Add `tests/unit/ai/review/test_checklist_corpus_schema.py`:
   - `--check` / in-memory equality against committed schema
   - assert schema enum sets match Python enums + `ALL_TAGS`
   - optional: validate both corpus files against the schema in-process (Pydantic
     `TypeAdapter` or an explicit direct `jsonschema` dependency — prefer reusing
     Pydantic already in-tree, or validate via the generator’s own structural checks
     plus loader tests, to avoid a new direct dep solely for this)
5. Document the regenerate command in the relevant contributor doc and/or corpus
   header.
6. Keep `loader.py` behavior unchanged; add no `lintro chk` tool wiring in v1.
7. Explicitly leave `.lintro-config.yaml` schema generation to a separate issue.

## Risks

| Risk | Severity | Mitigation |
| ---- | -------- | ---------- |
| Large `languages` enum (~311 identify tags) makes noisy diffs on `identify` bumps | Low–Med | Sorted emission; regenerating on dependency bumps is correct SSOT behavior |
| Editor without YAML schema support | Low | Loader + unit tests remain the CI backstop; docs list valid enums as supplement if desired |
| Schema/`if`/`then` incomplete vs loader | Low | Document intentional gaps (uniqueness); never remove loader checks |
| Contributors hand-edit `corpus.schema.json` | Med | File header + `--check` test failure message |
| Relative modeline path wrong after move | Low | Keep schema as fixed sibling of `corpus/`; test fixture asserts modeline substring |
| Scope creep into full config schema | Med | This doc’s scope call; separate issue |
