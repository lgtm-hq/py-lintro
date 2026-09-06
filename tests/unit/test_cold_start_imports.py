"""Cold-start ratchet for the ``lintro`` CLI entry point (#1305).

``import lintro.cli`` used to drag in the whole execution pipeline, the plugin
registry, pydantic and the optional ``lintro.ai`` subsystem, so even
``lintro --version`` paid for all of it. Subcommands now load on demand, and
these tests keep it that way.

Each check runs in a subprocess: once any module is in this process's
``sys.modules`` — pytest collection alone imports most of the package — an
in-process assertion proves nothing.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed argv against this interpreter; shell=False
import sys
from dataclasses import dataclass

from assertpy import assert_that

from lintro import __version__
from lintro.cli import _COMMAND_ALIASES, _COMMAND_MODULES

# Packages that must stay out of a bare `import lintro.cli`. Add to this list
# when a new heavy dependency appears on the cold path; never remove an entry
# to make a regression pass.
FORBIDDEN_COLD_START_MODULES: tuple[str, ...] = (
    "lintro.ai",
    "lintro.api",
    "lintro.plugins",
    "lintro.tools",
    "lintro.utils.tool_executor",
    "pydantic",
    "rich",
)

# Ceiling on the number of `lintro.*` modules a bare `import lintro.cli` may
# load. Four today (`lintro`, `lintro.cli`, `lintro.utils`,
# `lintro.utils.logger_setup`); the headroom absorbs a small refactor. Lower it
# when the number drops, never raise it.
MAX_COLD_START_LINTRO_MODULES = 8


@dataclass(frozen=True)
class _ProbeReport:
    """What a probe subprocess reports back about its own interpreter."""

    modules: tuple[str, ...]
    exit_code: int
    output: str


def _run_probe(script: str) -> _ProbeReport:
    """Run a probe script in a clean interpreter and parse its JSON report.

    Args:
        script: Python source that prints a single JSON object to stdout.

    Returns:
        _ProbeReport: The decoded report.
    """
    completed = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(completed.returncode).described_as(
        f"probe failed: {completed.stderr}",
    ).is_equal_to(0)
    payload = json.loads(completed.stdout)
    return _ProbeReport(
        modules=tuple(str(name) for name in payload["modules"]),
        exit_code=int(payload.get("exit_code", 0)),
        output=str(payload.get("output", "")),
    )


_IMPORT_PROBE = """
import json
import sys

import lintro.cli  # noqa: F401

print(json.dumps({"modules": sorted(sys.modules)}))
"""

_VERSION_PROBE = """
import json
import sys

from click.testing import CliRunner

from lintro.cli import cli

result = CliRunner().invoke(cli, ["--version"])
print(
    json.dumps(
        {
            "modules": sorted(sys.modules),
            "exit_code": result.exit_code,
            "output": result.output,
        },
    ),
)
"""


_HELP_PROBE = """
import json
import sys

from click.testing import CliRunner

from lintro.cli import cli

result = CliRunner().invoke(cli, ["--help"])
print(
    json.dumps(
        {
            "modules": sorted(sys.modules),
            "exit_code": result.exit_code,
            "output": result.output,
        },
    ),
)
"""


def test_importing_the_cli_loads_nothing_from_the_ai_package() -> None:
    """``import lintro.cli`` must not pull the optional AI subsystem in."""
    modules = _run_probe(_IMPORT_PROBE).modules

    ai_modules = [name for name in modules if name.startswith("lintro.ai")]

    assert_that(ai_modules).is_empty()


def test_importing_the_cli_skips_every_forbidden_module() -> None:
    """The cold path stays clear of the heavy packages listed above."""
    modules = _run_probe(_IMPORT_PROBE).modules

    loaded = [
        forbidden
        for forbidden in FORBIDDEN_COLD_START_MODULES
        if any(
            name == forbidden or name.startswith(f"{forbidden}.") for name in modules
        )
    ]

    assert_that(loaded).described_as(
        "modules that must stay off the `import lintro.cli` path",
    ).is_empty()


def test_cold_start_lintro_module_count_does_not_grow() -> None:
    """The number of ``lintro.*`` modules on the cold path stays bounded."""
    modules = _run_probe(_IMPORT_PROBE).modules

    lintro_modules = [name for name in modules if name.split(".")[0] == "lintro"]

    assert_that(len(lintro_modules)).described_as(
        f"cold-start lintro modules: {sorted(lintro_modules)}",
    ).is_less_than_or_equal_to(MAX_COLD_START_LINTRO_MODULES)


def test_version_command_loads_nothing_from_the_ai_package() -> None:
    """``lintro --version`` prints the version without importing ``lintro.ai``."""
    report = _run_probe(_VERSION_PROBE)

    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.output).contains(__version__)
    ai_modules = [name for name in report.modules if name.startswith("lintro.ai")]

    assert_that(ai_modules).is_empty()


def test_help_renders_every_lazily_loaded_command() -> None:
    """``lintro --help`` materializes the whole tree and renders its table.

    The help path is the one place that imports every subcommand module and
    builds the Rich table, so it catches a lazy table entry that names a
    missing module or attribute, and any annotation the deferred ``rich``
    import left undefined at runtime.
    """
    report = _run_probe(_HELP_PROBE)

    assert_that(report.exit_code).is_equal_to(0)
    for canonical in _COMMAND_MODULES:
        assert_that(report.output).contains(canonical)
    for alias in _COMMAND_ALIASES:
        assert_that(report.output).contains(alias)
