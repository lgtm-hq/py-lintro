# Pre-commit Integration

Lintro ships hook definitions for the [pre-commit](https://pre-commit.com)
framework so you can run quality checks and formatting automatically before
every commit in **your own** repository.

> This is opt-in. Adding lintro to your `.pre-commit-config.yaml` is entirely up
> to you — py-lintro itself does not enforce pre-commit.

## Quick Start

1. Install the pre-commit framework (once, per machine):

   ```bash
   pip install pre-commit
   # or: uv tool install pre-commit
   ```

2. Add lintro to your project's `.pre-commit-config.yaml`:

   ```yaml
   repos:
     - repo: https://github.com/lgtm-hq/py-lintro
       rev: v0.69.0 # pin to a released tag
       hooks:
         - id: lintro-check
   ```

3. Install the git hook and run it:

   ```bash
   pre-commit install       # wire lintro into your git commit flow
   pre-commit run --all-files
   ```

From now on, `git commit` runs `lintro check` against your staged files and
blocks the commit if any issues are found.

## Available Hooks

| Hook ID          | Runs             | Use it for                                    |
| ---------------- | ---------------- | --------------------------------------------- |
| `lintro-check`   | `lintro check`   | Fail the commit when quality issues are found |
| `lintro-format`  | `lintro format`  | Auto-fix formatting; re-stage and re-commit   |

Both hooks pass the staged filenames to lintro, so only the files you are
committing are inspected. lintro applies each underlying tool only to the file
types it supports, so a mixed set of staged files is fine.

### Check only (recommended default)

```yaml
repos:
  - repo: https://github.com/lgtm-hq/py-lintro
    rev: v0.69.0
    hooks:
      - id: lintro-check
```

### Auto-format then check

`lintro-format` rewrites files in place. When it changes a file, pre-commit
marks the run as failed so you can review and re-stage the result:

```yaml
repos:
  - repo: https://github.com/lgtm-hq/py-lintro
    rev: v0.69.0
    hooks:
      - id: lintro-format
      - id: lintro-check
```

### Scoping to specific files

Use pre-commit's standard `files`/`exclude`/`types` overrides to narrow a hook:

```yaml
repos:
  - repo: https://github.com/lgtm-hq/py-lintro
    rev: v0.69.0
    hooks:
      - id: lintro-check
        files: ^src/
```

## How it works

The hooks use `language: python`, so pre-commit builds an **isolated virtual
environment** and installs lintro from the pinned `rev`. This keeps lintro (and
its Python-based tools) independent of whatever is installed globally on the
machine.

Note that native, non-Python tools (for example `hadolint` or `shellcheck`)
are not installed into that isolated environment. lintro gracefully skips any
tool that is not available, so those checks simply do not run under the isolated
hook. If you rely on native tools in the hook, install lintro on the system and
override the hook to use it, for example:

```yaml
repos:
  - repo: local
    hooks:
      - id: lintro-check
        name: lintro check
        entry: lintro check
        language: system
        types: [file]
        require_serial: true
```

## Notes

- Always pin `rev:` to a released tag (not a branch) so hook runs are
  reproducible; `pre-commit autoupdate` can bump it for you.
- The first run is slower because pre-commit provisions the isolated
  environment; subsequent runs are cached.
