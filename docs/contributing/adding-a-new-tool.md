# Adding a New Tool to Lintro

This guide walks through every change required to add a new linting or formatting tool
to Lintro. Verify that each file path referenced below exists in the repository before
modifying it — this document reflects the current architecture; do not copy from stale
guides.

## Before You Start

**Read first:**

- [Contributing Guide](../contributing.md) — conventional commits, DCO sign-off, merge
  discipline
- [Style Guide](../style-guide.md) — code conventions, type hints, docstrings
- [Testing Guide](../testing.md) — test layout, staging pattern, fixtures

**Pick a reference implementation.** Do not write plugin/parser code from scratch.
Mirror the closest existing tool:

| Tool type              | Reference                                |
| ---------------------- | ---------------------------------------- |
| Simple linter (no fix) | `lintro/tools/definitions/actionlint.py` |
| Linter + formatter     | `lintro/tools/definitions/ruff.py`       |
| Security scanner       | `lintro/tools/definitions/bandit.py`     |
| Shell tool             | `lintro/tools/definitions/shellcheck.py` |

Read all files for that reference tool (definition, parser package, unit tests,
integration test, test samples) before writing any new code.

---

## Dogfooding Requirement (mandatory)

> **The tool must lint something real in this repository.** Every tool added to Lintro
> must be exercised on the repo's own code as part of the standard dogfooding runs.

In the **same PR** that adds the tool, do one of the following:

1. **Add repo configuration** — create or extend the tool's config file (e.g.
   `.shellcheckrc`, `.hadolint.yaml`) so the tool runs against the relevant source files
   in CI dogfooding jobs without errors.
2. **Add an allowlist entry** — if the tool genuinely cannot lint any file in this
   repository (e.g. it targets a language not present here), add an entry with a written
   rationale to the dogfood skip allowlist introduced in
   [#1510](https://github.com/lgtm-hq/py-lintro/issues/1510). A rationale-free entry
   will be rejected in review.

The dogfooding jobs (`docker-ci.yml` `dogfooding-quality` job and `dogfood-nightly.yml`)
verify this at merge time. See [`docs/lintro-self-use.md`](../lintro-self-use.md) for an
overview of how Lintro dogfoods its own codebase.

---

## Step 1 — Plugin definition

Create `lintro/tools/definitions/<tool>.py`.

Structure (mirrored from your reference tool):

```python
from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool

@register_tool
@dataclass
class <Tool>Plugin(BaseToolPlugin):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="<tool>",
            description="...",
            can_fix=False,
            tool_type=ToolType.LINTER,         # see ToolType options below
            file_patterns=["*.ext"],
            priority=50,                        # see DEFAULT_TOOL_PRIORITIES
            conflicts_with=[],
            native_configs=[".toolrc"],
            version_command=["<tool>", "--version"],
            min_version=get_min_version(ToolName.<TOOL>),
            default_options={"timeout": 30},
            default_timeout=30,
        )
```

**ToolType values** (may be combined with `|`):

| Value                     | When to use                          |
| ------------------------- | ------------------------------------ |
| `ToolType.LINTER`         | Code quality checker                 |
| `ToolType.FORMATTER`      | Code formatter                       |
| `ToolType.TYPE_CHECKER`   | Static type checker (mypy, tsc)      |
| `ToolType.DOCUMENTATION`  | Docstring/doc checker                |
| `ToolType.SECURITY`       | Security scanner (bandit, semgrep)   |
| `ToolType.INFRASTRUCTURE` | IaC/CI linter (hadolint, actionlint) |
| `ToolType.TEST_RUNNER`    | Test framework (pytest)              |

**Key implementation notes:**

- `check()` must return a `ToolResult`; call `self._prepare_execution(paths, options)`
  and check `ctx.should_skip` first.
- `fix()` raises `NotImplementedError` when `can_fix=False`.
- Always use list args in subprocess calls, never `shell=True`; add `# nosec B404` on
  the `import subprocess` line.
- `_prepare_execution()` handles file discovery and filtering by `file_patterns`. Use
  `ctx.rel_files` for the filtered list.
- If the tool has per-rule documentation URLs, implement `doc_url(self, code)` and add a
  `DocUrlTemplate` entry (see Step 3).

---

## Step 2 — Parser package

Create the three-file parser package in `lintro/parsers/<tool>/`:

```text
lintro/parsers/<tool>/__init__.py          # re-exports parser + issue class
lintro/parsers/<tool>/<tool>_issue.py      # issue dataclass (inherits BaseIssue)
lintro/parsers/<tool>/<tool>_parser.py     # parse_<tool>_output() function
```

The parser function signature must be:

```python
def parse_<tool>_output(output: str | None) -> list[<Tool>Issue]:
    ...
```

Issue classes inherit from `BaseIssue` and define `DISPLAY_FIELD_MAP` for custom column
labels. Mirror the issue and parser files from your reference tool exactly.

---

## Step 3 — Enums

### ToolName (required)

Add an entry to `lintro/enums/tool_name.py` in **alphabetical order**:

```python
class ToolName(StrEnum):
    ...
    <TOOL> = auto()
    ...
```

### DocUrlTemplate (if the tool has per-rule URLs)

Add an entry to `lintro/enums/doc_url_template.py`:

```python
class DocUrlTemplate(StrEnum):
    ...
    <TOOL> = "https://example.com/rules/{code}"
    ...
```

Use `{code}` as the placeholder for the (possibly normalized) rule identifier.

---

## Step 4 — Version management

Choose the path that matches the tool's distribution mechanism.

### Path A — Binary / Cargo / Rustup (e.g. shellcheck, hadolint, gitleaks)

1. **`lintro/_tool_versions.py`** — add to `TOOL_VERSIONS` dict:

   ```python
   TOOL_VERSIONS: dict[ToolName | str, str] = {
       ...
       ToolName.<TOOL>: "x.y.z",   # in alphabetical order
       ...
   }
   ```

2. **`lintro/tools/manifest.json`** — add a tool entry (version must match
   `_tool_versions.py`):

   ```json
   {
     "name": "<tool>",
     "version": "x.y.z",
     "install": { "type": "binary" },
     "tier": "tools",
     "category": "external",
     "version_command": ["<tool>", "--version"],
     "languages": ["<lang>"],
     "tags": ["linter"]
   }
   ```

3. **`renovate.json`** — add **two** custom manager entries (one for
   `_tool_versions.py`, one for `manifest.json`), copying the pattern from an existing
   binary tool. Both entries must reference the same upstream package on the same
   datasource so Renovate keeps them in sync.

4. **`scripts/utils/install-tools.sh`** — four sync points (see Step 8).

### Path B — npm (e.g. markdownlint-cli2, oxlint, prettier)

1. **`lintro/enums/tool_name.py`** — add `ToolName` member (already done in Step 3).

2. **`lintro/_tool_packages.py`** — add mapping in `NPM_PACKAGE_OWNERS`:

   ```python
   NPM_PACKAGE_OWNERS: dict[str, ToolName | None] = {
       ...
       "<npm-package>": ToolName.<TOOL>,
       ...
   }
   ```

   If the tool requires companion packages (plugins that ship alongside it), add them
   too with `None` as the value.

3. **`package.json`** — pin the package in `devDependencies` using a caret prefix:

   ```json
   "<npm-package>": "^x.y.z"
   ```

4. **Run the generator** (see Step 9) to regenerate `lintro/_generated_versions.py` and
   sync version fields in `manifest.json`.

5. **`lintro/tools/manifest.json`** — the generator writes the `version` field; verify
   the entry has the correct `install.type = "npm"` and `install.package`.

### Path C — Bundled Python (e.g. ruff, bandit, yamllint)

1. **`lintro/_tool_packages.py`** — add mapping in `PYPI_PACKAGE_OWNERS`:

   ```python
   PYPI_PACKAGE_OWNERS: dict[str, ToolName | None] = {
       ...
       "<pypi-package>": ToolName.<TOOL>,
       ...
   }
   ```

2. **`pyproject.toml`** — add to the appropriate optional-dependency group (typically
   `full`):

   ```toml
   [project.optional-dependencies]
   full = [
     ...
     "<pypi-package>>=x.y.z",
   ]
   ```

3. **Run the generator** (see Step 9).

4. **`lintro/tools/manifest.json`** — verify the generated entry has
   `install.type = "pip"` and `install.package = "<pypi-package>"`.

---

## Step 5 — Version parsing and install hints

### TOOLS_WITH_SIMPLE_VERSION_PATTERN

If the tool prints a plain version number (e.g. `1.2.3`) to stdout, add it to
`TOOLS_WITH_SIMPLE_VERSION_PATTERN` in `lintro/tools/core/version_parsing.py`. Tools
with non-standard version output (e.g. a multi-line header) require a custom extraction
branch in that same module.

### Install hints

Add the tool to `get_install_hints()` in `lintro/tools/core/version_checking.py` so
`lintro doctor` can display context-aware install instructions.

---

## Step 6 — DEFAULT_TOOL_PRIORITIES

The default priority for all tools is `50`. Only add an entry to
`DEFAULT_TOOL_PRIORITIES` in `lintro/utils/config_priority.py` if the tool needs a
non-default priority (e.g. formatters run first, type checkers run last). Check existing
entries before deciding on a value.

---

## Step 7 — pyproject.toml: package list

Add the new parser package to the `packages` list in `pyproject.toml` so it is included
in the wheel:

```toml
[tool.setuptools]
packages = [
  ...
  "lintro.parsers.<tool>",
  ...
]
```

---

## Step 8 — scripts/utils/install-tools.sh

Four places require editing (keep alphabetical order throughout):

1. **Help text** — add the tool to the description block in the `--help` section.
2. **`SUPPORTED_TOOLS` array** — add the tool's binary name.
3. **Installation block** — add a `should_install` block that reads the version via
   `get_tool_version "<tool>"` and installs from the appropriate upstream source. Copy
   the pattern from an existing tool of the same distribution type.
4. **`tools_to_verify` array** (near end of file) — add the binary name so the
   post-install verification step checks it.

---

## Step 9 — Run the version generator

After any version-related edits, run:

```bash
python3 scripts/ci/generate-tool-versions.py
```

This regenerates `lintro/_generated_versions.py` and syncs version fields in
`lintro/tools/manifest.json`. Verify the output is consistent and commit it alongside
the other changes.

To check without writing (useful before pushing):

```bash
python3 scripts/ci/generate-tool-versions.py --check
```

CI fails the PR if `_generated_versions.py` or `manifest.json` are out of sync.

---

## Step 10 — Docker

### `docker/tools.Dockerfile`

Add the tool's version verification to the `RUN` block at the end of the file (the block
that verifies every installed binary):

```dockerfile
<tool> --version && \
```

### Root `Dockerfile`

Add the tool's binary to **both** verify blocks in the root `Dockerfile`:

- **Root-user block** (around line 60): `<tool> --version && \`
- **Non-root block** (around line 78): `gosu lintro <tool> --version && \` (only needed
  for npm/bun-managed tools that install under a user prefix)

> **Note:** The root `Dockerfile` pulls the pre-built `lintro-tools` image (built from
> `docker/tools.Dockerfile`) via a digest-pinned `FROM`. Adding a tool to
> `tools.Dockerfile` is the primary change; the root `Dockerfile`'s verify blocks are a
> second guard.

---

## Step 11 — Tests

### Unit tests — parser

```text
tests/unit/parsers/test_<tool>_parser.py
```

Cover at minimum: single-issue output, multi-issue output, empty/None output,
`to_display_row()` format, and at least one edge case (e.g. truncated line, unknown
severity). Mirror the structure from your reference tool's parser test.

### Unit tests — plugin

```text
tests/unit/tools/<tool>/__init__.py
tests/unit/tools/<tool>/test_options.py        # definition attrs, default opts, set_options
tests/unit/tools/<tool>/test_execution.py      # check/fix with mocked subprocess
tests/unit/tools/<tool>/test_error_handling.py # (if your reference has one)
```

Mock the version check:

```python
patch("lintro.plugins.execution_preparation.verify_tool_version", return_value=None)
```

Mock subprocess calls:

```python
patch.object(plugin, "_run_subprocess", return_value=(True, "<output>"))
```

### Integration tests

```text
tests/integration/tools/test_<tool>_integration.py
```

or a subdirectory:

```text
tests/integration/tools/<tool>/
    __init__.py
    conftest.py
    test_check.py
    test_definition.py
    test_options.py
```

Use `pytest.mark.skipif(shutil.which("<tool>") is None, reason="<tool> not installed")`
(or the `skip_if_tool_unavailable` fixture) to gate integration tests. Integration tests
must pass in CI/Docker where every tool is installed.

### Test samples

```text
test_samples/tools/<language>/<tool>/<tool>_violations.<ext>
test_samples/tools/<language>/<tool>/<tool>_clean.<ext>
```

The violations file must trigger real tool errors when run directly:
`<tool> <violations_file>`. The clean file must pass without errors.

`test_samples/` is listed in `.lintro-ignore` so Lintro never lints it during
dogfooding; tests that scan a sample file must stage a copy into a temp directory (see
the staging pattern in [`docs/testing.md`](../testing.md)).

---

## Step 12 — Documentation

### README.md

Add a row to the **Supported Tools** table near the top of `README.md`.

### docs/configuration.md

Add a full configuration section covering:

- Tool description and purpose
- Installation instructions (all distribution methods)
- Native config file example and location
- Available `--tool-options` table (CLI flags only — do not document config-file-only
  options as `--tool-options`; they will cause runtime errors)
- Usage examples

### docs/getting-started.md

Add the tool to the **Optional External Tools** section with install instructions and a
short usage example.

### docs/tool-analysis/\<tool\>-analysis.md

Create a tool analysis document following the standard format from existing analyses:

- Overview and purpose
- Core tool capabilities (native features)
- Lintro implementation analysis: preserved features, limitations, enhancements
- Usage comparison (native vs lintro commands)
- Configuration strategy and priority
- Recommendations

### apps/site (docs website)

The docs website at `apps/site` is **auto-mirrored** from `docs/` by
`apps/site/scripts/migrate-docs-content.py`; do not edit files under
`apps/site/src/content/docs/` directly. Changes in `docs/` propagate automatically.

---

## Step 13 — Homebrew (optional, for Homebrew-installable tools)

If the tool is available as a Homebrew formula and its version matches what
`_tool_versions.py` pins:

1. Add `depends_on "<tool>"` to `scripts/ci/homebrew/templates/lintro.rb.template` in
   alphabetical order under the appropriate category.
2. Update the caveats section in the same file to list the new tool.
3. Bundled Python tools (ruff, black, mypy, bandit, yamllint) are excluded from the
   Homebrew venv — they install as separate Homebrew formulae and are discovered via
   `PATH`, not `python -m`.

> If the Homebrew package version does not match the pinned version in
> `_tool_versions.py`, omit `depends_on` and document alternative install instructions
> in `docs/configuration.md`.

---

## Definition of Done

A new-tool PR is **not mergeable** until all three gates pass:

| Gate                                                                                   | What it checks                                                                                                                                                                                     |
| -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [**#1509**](https://github.com/lgtm-hq/py-lintro/issues/1509) — plugin completeness    | Parametrized test suite asserts that every registered plugin has an integration surface, `tool_type`/manifest tags agree, `DEFAULT_TOOL_PRIORITIES` entry is consistent, and docs references exist |
| [**#1510**](https://github.com/lgtm-hq/py-lintro/issues/1510) — dogfood skip allowlist | Dogfooding CI fails if any enabled tool reports SKIP without an entry in the committed allowlist; every allowlist entry must have a written rationale                                              |
| [**#1511**](https://github.com/lgtm-hq/py-lintro/issues/1511) — manifest vs image      | `scripts/ci/verify-manifest-tools.py` runs inside the freshly built CI image; if the manifest declares the tool but the image cannot execute its `version_command`, the build fails                |

Until those gates are live, satisfy their intent manually using the
[`lintro-verify` checklist](https://github.com/lgtm-hq/py-lintro/blob/main/skills-ref/lintro-verify/SKILL.md)
and the [pre-submit checklist](#pre-submit-checklist) below.

---

## Pre-submit checklist

Run these locally before opening a PR:

```bash
# Format and lint
uv run lintro fmt && uv run lintro chk

# Unit tests
uv run pytest tests/unit/parsers/test_<tool>_parser.py tests/unit/tools/<tool>/ -v

# Full suite (stops at 3 failures by default; use --maxfail=0 to see all)
uv run pytest --maxfail=0

# Verify the tool appears and doctor shows a clean version
uv run lintro list-tools | grep <tool>
uv run lintro doctor

# Verify the version generator is in sync (no diff = clean)
python3 scripts/ci/generate-tool-versions.py --check
```

Implementation checklist:

- [ ] `lintro/tools/definitions/<tool>.py` — `@register_tool`, `BaseToolPlugin`,
      `ToolDefinition`
- [ ] `lintro/parsers/<tool>/` — `__init__.py`, `<tool>_issue.py`, `<tool>_parser.py`
- [ ] `lintro/enums/tool_name.py` — `ToolName.<TOOL>` (alphabetical)
- [ ] `lintro/enums/doc_url_template.py` — `DocUrlTemplate.<TOOL>` (if applicable)
- [ ] Version registration (Path A / B / C, see Step 4)
- [ ] `lintro/tools/manifest.json` — tool entry with correct version and install type
- [ ] `lintro/tools/core/version_parsing.py` — `TOOLS_WITH_SIMPLE_VERSION_PATTERN` (if
      applicable)
- [ ] `lintro/tools/core/version_checking.py` — install hints
- [ ] `lintro/utils/config_priority.py` — `DEFAULT_TOOL_PRIORITIES` (if non-default)
- [ ] `pyproject.toml` — parser package added to `packages` list
- [ ] `scripts/utils/install-tools.sh` — 4 sync points (help, SUPPORTED_TOOLS, install
      block, tools_to_verify)
- [ ] `docker/tools.Dockerfile` — verify step
- [ ] `Dockerfile` — root block and non-root block (for npm/bun tools)
- [ ] `renovate.json` — custom managers for `_tool_versions.py` and `manifest.json`
      (binary tools only)
- [ ] `scripts/ci/generate-tool-versions.py --check` passes
- [ ] Unit tests (parser + plugin) added
- [ ] Integration tests added (with `skipif` guard)
- [ ] Test samples added (`violations.<ext>` and `clean.<ext>`)
- [ ] README.md — Supported Tools table row added
- [ ] `docs/configuration.md` — full configuration section
- [ ] `docs/getting-started.md` — Optional External Tools entry
- [ ] `docs/tool-analysis/<tool>-analysis.md` — tool analysis document
- [ ] **Dogfooding** — repo config added **or** allowlist entry with rationale (#1510)
- [ ] `uv run lintro fmt && uv run lintro chk` passes
- [ ] `uv run pytest --maxfail=0` passes
- [ ] Homebrew template updated (if Homebrew-installable)
