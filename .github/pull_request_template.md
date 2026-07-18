<!-- markdownlint-disable MD041 -- PR template does not start with a top-level heading -->

## Commit Summary (Conventional Commits)

- Title (required, present tense):

  ```text
  <type>(optional-scope)!: concise summary
  ```

  Examples: `feat(cli): add --group-by`, `fix(parser): handle empty config`,
  `refactor(core)!: rewrite engine`

- Type:
  - [ ] feat (minor)
  - [ ] fix / perf (patch)
  - [ ] docs
  - [ ] refactor
  - [ ] test
  - [ ] chore / ci / style

- Breaking change:
  - [ ] `!` in title or `BREAKING CHANGE:` footer included

### Release Trigger Rules (exact)

- A merged PR will bump the version based on its title (squash merge required):
  - `feat(...)` or `feat:` → MINOR bump
  - `fix(...)` / `fix:` or `perf(...)` / `perf:` → PATCH bump
  - Any title with `!` after the type (e.g. `feat!:` or `feat(scope)!:`) or a body
    containing `BREAKING CHANGE:` → MAJOR bump
- Use squash merge so the PR title becomes the merge commit title.
- Valid examples:
  - `feat(cli): add --group-by`
  - `fix(parser): handle empty config`
  - `perf: optimize grouping performance`
  - `feat(api)!: remove deprecated flags`

## What’s Changing

Describe the changes and why.

## Checklist

- [ ] Title follows Conventional Commits
- [ ] Tests added/updated
- [ ] Docs updated if user-facing
- [ ] Local CI passed (`./scripts/local/run-tests.sh`)

## Related Issues

<!-- Replace # with actual issue numbers, e.g., Closes #123, Fixes #456, Related #789 -->

Closes # | Fixes # | Related #

## Details

Implementation notes, migration/breaking notes, and testing strategy.

<!-- ============================================================ -->
<!-- NEW TOOL? Delete this section if this PR does not add a tool -->
<!-- ============================================================ -->

## New Tool Checklist

_Complete this section when the PR adds a new linting or formatting tool. Delete it for
all other PR types._

See the full guide:
[`docs/contributing/adding-a-new-tool.md`](../docs/contributing/adding-a-new-tool.md)

### Core implementation

- [ ] `lintro/tools/definitions/<tool>.py` — `@register_tool`, `BaseToolPlugin`,
      `ToolDefinition`
- [ ] `lintro/parsers/<tool>/` — `__init__.py`, issue class, parser function
- [ ] `lintro/enums/tool_name.py` — `ToolName.<TOOL>` added
- [ ] Version registered in correct source (`_tool_versions.py` / `_tool_packages.py` +
      `package.json` / `pyproject.toml`) and `manifest.json` matches
- [ ] `scripts/ci/generate-tool-versions.py --check` passes

### Dogfooding (required — gate [#1510](https://github.com/lgtm-hq/py-lintro/issues/1510))

- [ ] Repo config added so the tool runs against this repo's own files, **or**
- [ ] Dogfood skip allowlist entry added with written rationale

### Infrastructure

- [ ] `scripts/utils/install-tools.sh` — 4 sync points updated
- [ ] `docker/tools.Dockerfile` and root `Dockerfile` verify blocks updated
- [ ] `renovate.json` custom managers added (binary tools only)

### Tests and docs

- [ ] Unit tests (parser + plugin) and integration test added
- [ ] Test samples added (`violations.<ext>` and `clean.<ext>`)
- [ ] README.md Supported Tools table updated
- [ ] `docs/configuration.md`, `docs/getting-started.md`, and
      `docs/tool-analysis/<tool>-analysis.md` updated
- [ ] `uv run lintro fmt && uv run lintro chk` green
- [ ] `uv run pytest --maxfail=0` green
