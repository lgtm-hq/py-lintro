# Library API

Lintro exposes a small, stable Python API for embedding checks, formatting, and tests in
your own programs.

```python
from lintro.api import check, fmt, test

result = check(paths=["src"], tools="ruff")
if not result.success:
    raise SystemExit(result.exit_code)
```

Each entry point returns a `LintroResult`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class LintroResult:
    action: str      # "check", "fmt", or "test"
    exit_code: int   # 0 on success, non-zero when issues/errors occur

    @property
    def success(self) -> bool: ...
```

Available functions (all keyword-only):

- `check(...)` — run linting/quality tools against paths.
- `format(...)` — auto-fix formatting issues. Also exported as `fmt`.
- `test(...)` — run pytest through Lintro's output formatting.

Exceptions raised during execution propagate normally to the caller, and tool output is
written directly to the console (no buffering).

## Migration note

Previous releases exposed thin programmatic wrappers in the command modules
(`lintro.cli_utils.commands.check.check`,
`lintro.cli_utils.commands.format.format_code`, and
`lintro.cli_utils.commands.test.test`). Internally these invoked the Click commands
through `click.testing.CliRunner`, a test helper that swallowed exceptions into a result
object and buffered stdout/stderr.

Those wrappers still exist for backward compatibility, but now delegate to the real
library API in `lintro.api`. External callers should migrate to `lintro.api.check` /
`lintro.api.format` (`fmt`) / `lintro.api.test`, which:

- return a structured `LintroResult` instead of a Click `Result`;
- let exceptions propagate instead of capturing them; and
- do not redirect or buffer console output.

`CliRunner` is no longer used anywhere in the `lintro` package; it remains only in the
test suite, where it is the appropriate tool for exercising the CLI.
