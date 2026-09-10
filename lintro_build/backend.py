"""In-tree PEP 517 build backend that generates the version artifacts.

Declared via ``[build-system] build-backend = "lintro_build.backend"`` with
``backend-path = ["."]``. Every hook delegates to ``setuptools.build_meta``
after ensuring the three derived artifacts exist
(``lintro/_generated_versions.py``, ``lintro/tools/manifest.json``,
``lintro/plugins/_builtin_index.py``), which stopped being committed in epic
#2176 phase 4.

Sdist-baked-outputs rule: the generator inputs ``package.json`` and
``requirements-semgrep.txt`` are deliberately not shipped in the sdist, but
``build_sdist`` bakes the generated outputs in. So:

* inputs present (repo checkout) -> generate;
* outputs present and inputs absent (wheel built from an unpacked sdist) ->
  skip and trust the baked outputs;
* neither -> fail loudly.

Stdlib-only apart from the setuptools delegation, like the rest of
``lintro_build``.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any, cast

from .exit_codes import EXIT_OK
from .versions.paths import GeneratorPaths


def _delegate() -> ModuleType:
    """Import and return the setuptools PEP 517 backend.

    Imported lazily: setuptools exists in the isolated build environment
    (``[build-system] requires``) but not necessarily in the dev virtualenv
    where this module is imported by tests.

    Returns:
        The ``setuptools.build_meta`` module.
    """
    from setuptools import build_meta

    # setuptools ships untyped here; the module object is what we return.
    return cast(ModuleType, build_meta)


# Fully derived artifacts relative to the repo root. The rendered manifest is
# checked via GeneratorPaths so the list stays in one place.
_INPUT_FILES = (
    "package.json",
    "requirements-semgrep.txt",
)


class BuildInputsMissingError(RuntimeError):
    """Raised when neither generator inputs nor baked outputs exist."""


def _artifact_paths(repo_root: Path) -> tuple[Path, ...]:
    """Return the three generated artifact paths for a repo root.

    Args:
        repo_root: Source tree the build runs from.

    Returns:
        Paths of the derived artifacts.
    """
    paths = GeneratorPaths.from_repo_root(repo_root)
    return (
        paths.generated_path,
        paths.manifest_path,
        repo_root / "lintro" / "plugins" / "_builtin_index.py",
    )


def _ensure_artifacts(repo_root: Path) -> None:
    """Generate the derived artifacts, or trust baked sdist outputs.

    Args:
        repo_root: Source tree the build runs from (the process cwd under
            PEP 517).

    Raises:
        BuildInputsMissingError: When neither the generator inputs nor the
            baked outputs are present, or generation fails.
    """
    from . import generate_all

    inputs_present = all((repo_root / name).exists() for name in _INPUT_FILES)
    if inputs_present:
        rc = generate_all(repo_root)
        if rc != EXIT_OK:
            raise BuildInputsMissingError(
                f"version-artifact generation failed with exit code {rc}; "
                f"see the generator error above",
            )
        return

    outputs_present = all(path.exists() for path in _artifact_paths(repo_root))
    if outputs_present:
        # Wheel built from an unpacked sdist: build_sdist baked the outputs
        # and the inputs are deliberately absent. Trust the baked copies.
        return

    raise BuildInputsMissingError(
        "cannot build lintro: generator inputs (package.json, "
        "requirements-semgrep.txt) are absent and the generated artifacts "
        "(_generated_versions.py, tools/manifest.json, "
        "plugins/_builtin_index.py) are not baked into this tree. Build from "
        "a repository checkout or an official sdist.",
    )


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build a wheel after ensuring the derived artifacts exist.

    Args:
        wheel_directory: Directory to place the wheel in.
        config_settings: Backend configuration settings.
        metadata_directory: Prepared metadata directory, if any.

    Returns:
        Basename of the built wheel.
    """
    _ensure_artifacts(Path.cwd())
    return cast(
        str,
        _delegate().build_wheel(
            wheel_directory,
            config_settings=config_settings,
            metadata_directory=metadata_directory,
        ),
    )


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Build an sdist after ensuring the derived artifacts exist.

    Args:
        sdist_directory: Directory to place the sdist in.
        config_settings: Backend configuration settings.

    Returns:
        Basename of the built sdist.
    """
    _ensure_artifacts(Path.cwd())
    return cast(
        str,
        _delegate().build_sdist(
            sdist_directory,
            config_settings=config_settings,
        ),
    )


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build an editable wheel after ensuring the derived artifacts exist.

    Args:
        wheel_directory: Directory to place the editable wheel in.
        config_settings: Backend configuration settings.
        metadata_directory: Prepared metadata directory, if any.

    Returns:
        Basename of the built editable wheel.
    """
    _ensure_artifacts(Path.cwd())
    return cast(
        str,
        _delegate().build_editable(
            wheel_directory,
            config_settings=config_settings,
            metadata_directory=metadata_directory,
        ),
    )


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Prepare wheel metadata, ensuring the derived artifacts exist first.

    Args:
        metadata_directory: Directory to place the metadata in.
        config_settings: Backend configuration settings.

    Returns:
        Basename of the metadata directory.
    """
    _ensure_artifacts(Path.cwd())
    return cast(
        str,
        _delegate().prepare_metadata_for_build_wheel(
            metadata_directory,
            config_settings=config_settings,
        ),
    )


def prepare_metadata_for_build_editable(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Prepare editable metadata, ensuring the derived artifacts exist first.

    Args:
        metadata_directory: Directory to place the metadata in.
        config_settings: Backend configuration settings.

    Returns:
        Basename of the metadata directory.
    """
    _ensure_artifacts(Path.cwd())
    return cast(
        str,
        _delegate().prepare_metadata_for_build_editable(
            metadata_directory,
            config_settings=config_settings,
        ),
    )


def get_requires_for_build_wheel(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    """Return the build requirements for a wheel build.

    Args:
        config_settings: Backend configuration settings.

    Returns:
        Additional build requirements.
    """
    return cast(
        "list[str]",
        _delegate().get_requires_for_build_wheel(
            config_settings=config_settings,
        ),
    )


def get_requires_for_build_sdist(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    """Return the build requirements for an sdist build.

    Args:
        config_settings: Backend configuration settings.

    Returns:
        Additional build requirements.
    """
    return cast(
        "list[str]",
        _delegate().get_requires_for_build_sdist(
            config_settings=config_settings,
        ),
    )


def get_requires_for_build_editable(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    """Return the build requirements for an editable build.

    Args:
        config_settings: Backend configuration settings.

    Returns:
        Additional build requirements.
    """
    return cast(
        "list[str]",
        _delegate().get_requires_for_build_editable(
            config_settings=config_settings,
        ),
    )
