# PR #931 Findings: Lightweight Installs

All findings from the initial review and code review comments have been addressed. The
implementation now covers all 11 phases of the plan.

## Resolved

### Parser failure visibility — now wired through

`safe_parse_items_with_stats()` no longer counts intentional `None` returns as parse
failures (only exceptions and non-dict items count). `_display_fix_result()` now
forwards `result.parse_failures_count` to `print_tool_result()`, and
`create_json_output()` includes `parse_failures_count` per tool result.

### `lintro install --write-lock` — complete lock with all plan entries

The lock is now written after displaying the plan. `InstallLockEntry` includes a
`status` field (`to_install`, `to_upgrade`, `ok`, `outdated`, `manual`, `skipped`), and
all plan sections are serialized into the lock.

### Manual install bucket — prerequisite failures routed correctly

`_check_prerequisites()` failures (missing cargo, npm, etc.) now go to `plan.manual`
instead of `plan.skipped`. Tests updated accordingly.

### `lintro init` — conservative merge on rerun

When the config file exists and `--force` is not provided, `lintro init` parses the
existing YAML and merges only new tool entries and `enabled_tools` additions, preserving
user-managed keys. `--force` still replaces the file entirely.

### Execution-time version tolerance warning

`verify_tool_version()` now emits a loguru warning when the installed version is
`>= min_version` but `< recommended_version`. The tool still runs (not skipped).

### Manifest validation enforces `min_version <= version`

`ToolRegistry._parse_tool_entry()` validates the ordering and clamps `min_version` to
`version` with a warning if invalid.

### Interactive install flow — tool-level selection

`_interactive_select()` now shows the resolved tool list after profile selection and
offers a `[c]ustomize` option for per-tool toggle. Also fixed: the interactive prompt no
longer fires when `--profile` or `--all` is explicitly supplied.

### Doctor: INCOMPATIBLE in report and fixable logic

`status_icon` mapping now includes `INCOMPATIBLE` and `DISABLED`. `has_fixable`,
`affected_names`, and `_run_fix` all include `INCOMPATIBLE`. `incompatible_count` is
recomputed after `--fix` recheck.

### YAML lock file — explicit error instead of silent JSON fallback

`write_install_lock()` raises `ImportError` with clear instructions when YAML is
requested but PyYAML is not installed.

### `min_version` docstring reconciled

`ManifestTool.min_version` docstring updated to state the field is required (set equal
to `version` when compatibility range is not yet proven).

### Homebrew sed pattern fixed

The replacement in `generate-pypi-formula.sh` now matches the full template description
string so both the class rename and description update succeed.

### Documentation updated

- `docs/configuration.md`: Added sections for `lintro init`, doctor config filtering,
  and install lock/export usage.
- `CHANGELOG.md`: Added phase-by-phase entries under `[Unreleased]`.
- `docs/contributing.md`: Already had `min_version` rules (from the PR).

### Redundant import removed

Duplicate `from pathlib import Path` removed from `install.py`.

### Tests updated

- `test_compare_versions`: Added `INCOMPATIBLE` parametrize case.
- `test_built_package.py`: Full extra test now checks all 6 bundled tools.
- `test_tool_installer.py`: Prerequisite tests assert `plan.manual`.
- `test_init_command.py`: Updated for merge-on-rerun behavior.

## Validation Performed

```text
uv run pytest tests/unit/ tests/config/ -x -q --timeout=60
# 4596 passed, 8 skipped, 0 failures

uv run ruff check <all changed files>
# All checks passed
```
