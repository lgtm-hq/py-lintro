# Pre-commit Integration

Lintro ships hook definitions for the [pre-commit](https://pre-commit.com) framework so
you can run quality checks and formatting automatically before every commit in **your
own** repository.

> This is opt-in. Adding lintro to your `.pre-commit-config.yaml` is entirely up to you
> — py-lintro itself does not enforce pre-commit.

The default hooks use your **system-installed lintro** (`language: system`). Lintro is
written in Python but it's not a Python-only tool: it drives native binaries such as
`hadolint` and `shellcheck` that a pip-isolated hook environment cannot provide, so a
system install gives you the full tool set.

## Quick Start

1. Install lintro so it is on your `PATH` (once, per machine):

   ```bash
   uv tool install lintro
   # or: brew tap lgtm-hq/tap && brew install lintro
   # or: npm install -g lintro
   ```

2. Install the pre-commit framework (once, per machine):

   ```bash
   pip install pre-commit
   # or: uv tool install pre-commit
   ```

3. Add lintro to your project's `.pre-commit-config.yaml`:

   ```yaml
   repos:
     - repo: https://github.com/lgtm-hq/py-lintro
       rev: v0.78.2 # pin to a released tag (auto-updated on release)
       hooks:
         - id: lintro-check
   ```

4. Install the git hook and run it:

   ```bash
   pre-commit install       # wire lintro into your git commit flow
   pre-commit run --all-files
   ```

From now on, `git commit` runs `lintro check` against your staged files and blocks the
commit if any issues are found.

## Available Hooks

| Hook ID                | Runs            | Environment                                   |
| ---------------------- | --------------- | --------------------------------------------- |
| `lintro-check`         | `lintro check`  | System lintro (full tool set) — **default**   |
| `lintro-format`        | `lintro format` | System lintro (full tool set) — **default**   |
| `lintro-check-python`  | `lintro check`  | Isolated Python env (Python-based tools only) |
| `lintro-format-python` | `lintro format` | Isolated Python env (Python-based tools only) |

All hooks pass the staged filenames to lintro, so only the files you are committing are
inspected. lintro applies each underlying tool only to the file types it supports, so a
mixed set of staged files is fine.

### Check only (recommended default)

```yaml
repos:
  - repo: https://github.com/lgtm-hq/py-lintro
    rev: v0.78.2
    hooks:
      - id: lintro-check
```

### Auto-format then check

`lintro-format` rewrites files in place. When it changes a file, pre-commit marks the
run as failed so you can review and re-stage the result:

```yaml
repos:
  - repo: https://github.com/lgtm-hq/py-lintro
    rev: v0.78.2
    hooks:
      - id: lintro-format
      - id: lintro-check
```

### Scoping to specific files

Use pre-commit's standard `files`/`exclude`/`types` overrides to narrow a hook:

```yaml
repos:
  - repo: https://github.com/lgtm-hq/py-lintro
    rev: v0.78.2
    hooks:
      - id: lintro-check
        files: ^src/
```

## Alternative: isolated Python environment

If you prefer that pre-commit manage the lintro installation itself — no system install
required — use the `-python` hook variants:

```yaml
repos:
  - repo: https://github.com/lgtm-hq/py-lintro
    rev: v0.78.2
    hooks:
      - id: lintro-check-python
```

These use `language: python`, so pre-commit builds an isolated virtual environment and
installs lintro from the pinned `rev`. This is hermetic and reproducible, but comes with
an important limitation:

> **Native tools do not run in the isolated env.** Only lintro's Python-based tools
> (ruff, yamllint, ...) are available there. Native binaries such as `hadolint`,
> `shellcheck`, or `prettier` are not installed into the hook environment, and lintro
> will skip them. If you rely on those checks, use the default system hooks instead.

The first run of a `-python` hook is also slower because pre-commit provisions the
environment (installing lintro and its dependencies from the repo); subsequent runs are
cached.

## Notes

- The default (`system`) hooks require lintro on `PATH`; pre-commit fails with an
  "Executable `lintro` not found" error otherwise — install it first (see Quick Start
  step 1).
- Always pin `rev:` to a released tag (not a branch) so hook runs are reproducible;
  `pre-commit autoupdate` can bump it for you. In this repository, example pins in this
  doc are refreshed automatically when the release Version-PR runs.
- With `language: system`, the `rev:` selects which hook _definitions_ are used; the
  lintro _version_ that runs is whatever is installed on the machine. With the `-python`
  variants, `rev:` also pins the lintro version itself.
