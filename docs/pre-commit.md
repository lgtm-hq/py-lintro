# Pre-commit Integration

Lintro ships hook definitions for the [pre-commit](https://pre-commit.com)
framework so you can run quality checks and formatting automatically before
every commit in **your own** repository.

> This is opt-in. Adding lintro to your `.pre-commit-config.yaml` is entirely up
> to you — py-lintro itself does not enforce pre-commit.

The default hooks use your **system-installed lintro** (`language: system`).
Lintro is written in Python but it's not a Python-only tool: it drives native
binaries such as `hadolint` and `shellcheck` that a pip-isolated hook
environment cannot provide, so a system install gives you the full tool set.

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
       rev: v0.69.0 # pin to a released tag
       hooks:
         - id: lintro-check
   ```

4. Install the git hook and run it:

   ```bash
   pre-commit install       # wire lintro into your git commit flow
   pre-commit run --all-files
   ```

From now on, `git commit` runs `lintro check` against your staged files and
blocks the commit if any issues are found.

## Available Hooks

| Hook ID                | Runs            | Environment                                   |
| ---------------------- | --------------- | --------------------------------------------- |
| `lintro-check`         | `lintro check`  | System lintro (full tool set) — **default**   |
| `lintro-format`        | `lintro format` | System lintro (full tool set) — **default**   |
| `lintro-check-python`  | `lintro check`  | Isolated Python env (Python-based tools only) |
| `lintro-format-python` | `lintro format` | Isolated Python env (Python-based tools only) |

All hooks pass the staged filenames to lintro, so only the files you are
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

## Hermetic hooks: the `lintro-pre-commit` mirror (recommended)

If you want pre-commit to manage lintro itself — no system install required —
use the dedicated mirror repository
[`lgtm-hq/lintro-pre-commit`](https://github.com/lgtm-hq/lintro-pre-commit):

```yaml
repos:
  - repo: https://github.com/lgtm-hq/lintro-pre-commit
    rev: v0.69.0 # matches the pinned lintro version
    hooks:
      - id: lintro-check
      # - id: lintro-format  # opt in for auto-formatting
```

The mirror is a tiny repo whose only job is to pin the published `lintro` wheel.
pre-commit installs that wheel from [PyPI](https://pypi.org/project/lintro/) into
its isolated environment, so the hook provisions in **seconds** and never builds
py-lintro from source. This is the endorsed hermetic path — the same model
[astral-sh/ruff-pre-commit](https://github.com/astral-sh/ruff-pre-commit) uses.

The mirror's `rev:` tracks the lintro version it pins; each py-lintro release
publishes a matching mirror tag automatically.

## Alternative: isolated Python environment from this repo

The `-python` hook variants also run in a pre-commit-managed Python env, but
they install lintro by building **py-lintro from source** at the pinned `rev`
(pre-commit clones this repository and runs a source build). That is slower and
heavier than the mirror above; prefer the mirror for hermetic hooks and reach
for these variants only if you specifically need to run against unreleased
py-lintro code at a given `rev`:

```yaml
repos:
  - repo: https://github.com/lgtm-hq/py-lintro
    rev: v0.69.0
    hooks:
      - id: lintro-check-python
```

Both the mirror and the `-python` variants use `language: python`, so they share
one important limitation:

> **Native tools do not run in the isolated env.** Only lintro's Python-based
> tools (ruff, yamllint, ...) are available there. Native binaries such as
> `hadolint`, `shellcheck`, or `prettier` are not installed into the hook
> environment, and lintro will skip them. If you rely on those checks, use the
> default system hooks instead.

The first run of an isolated hook is also slower because pre-commit provisions
the environment; subsequent runs are cached.

## Notes

- The default (`system`) hooks require lintro on `PATH`; pre-commit fails with
  an "Executable `lintro` not found" error otherwise — install it first (see
  Quick Start step 1).
- Always pin `rev:` to a released tag (not a branch) so hook runs are
  reproducible; `pre-commit autoupdate` can bump it for you.
- With `language: system`, the `rev:` selects which hook _definitions_ are
  used; the lintro _version_ that runs is whatever is installed on the machine.
  With the mirror and the `-python` variants, `rev:` also pins the lintro
  version itself.
