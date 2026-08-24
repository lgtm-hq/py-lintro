"""Tests for the typos plugin definition and metadata."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro._tool_versions import get_tool_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.tools.core.install_strategies.package_names import ecosystem_package_name
from lintro.tools.core.tool_registry import ManifestRegistry
from lintro.tools.core.version_parsing import (
    TOOLS_WITH_SIMPLE_VERSION_PATTERN,
    extract_version_from_output,
    get_install_hints,
    get_minimum_versions,
)
from lintro.tools.definitions.typos import (
    BINARY_PATH_SUFFIXES,
    TYPOS_DEFAULT_TIMEOUT,
    TyposPlugin,
)


def test_definition_basic_metadata(typos_plugin: TyposPlugin) -> None:
    """The definition exposes the expected identity and capabilities.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    definition = typos_plugin.definition

    assert_that(definition.name).is_equal_to("typos")
    assert_that(definition.can_fix).is_true()
    assert_that(definition.tool_type).is_equal_to(ToolType.LINTER)


def test_definition_file_patterns_match_all(typos_plugin: TyposPlugin) -> None:
    """Typos inspects all text files via a catch-all pattern.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    assert_that(typos_plugin.definition.file_patterns).is_equal_to(["*"])


def test_definition_native_configs(typos_plugin: TyposPlugin) -> None:
    """No-config selection only looks at standalone typos config files.

    crate-ci/typos also reads ``[tool.typos]`` in ``pyproject.toml`` and
    ``[package.metadata.typos]`` / ``[workspace.metadata.typos]`` in
    ``Cargo.toml``. Those files must stay off ``native_configs``: listing
    them would auto-select the plugin on every Python or Rust tree.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    assert_that(typos_plugin.definition.native_configs).is_equal_to(
        ["typos.toml", ".typos.toml", "_typos.toml"],
    )


def test_default_timeout_option(typos_plugin: TyposPlugin) -> None:
    """The default timeout option is applied.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    assert_that(typos_plugin.options.get("timeout")).is_equal_to(TYPOS_DEFAULT_TIMEOUT)


def test_build_command_uses_json_format(typos_plugin: TyposPlugin) -> None:
    """The base command requests JSON output for reliable parsing.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    cmd = typos_plugin._build_command()

    assert_that(cmd).is_equal_to(["typos", "--format", "json", "--force-exclude"])


def test_build_command_forces_excludes(typos_plugin: TyposPlugin) -> None:
    """``--force-exclude`` keeps .typos.toml excludes effective.

    typos skips its ignore rules for paths named on the command line unless
    this flag is set, and lintro always passes an explicit file list.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    assert_that(typos_plugin._build_command()).contains("--force-exclude")


def test_text_files_drops_binary_files(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Binary files are filtered out before typos is invoked.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    (tmp_path / "notes.txt").write_text("plain text\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00binary")

    kept = typos_plugin._text_files(
        files=["notes.txt", "logo.png"],
        cwd=str(tmp_path),
    )

    assert_that(kept).is_equal_to(["notes.txt"])


def test_text_files_drops_known_binary_suffixes_without_nul(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """JPEG/PDF-style headers have no NUL in the first 8 KiB.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    (tmp_path / "notes.txt").write_text("plain text\n")
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 100)
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.7\n" + b"A" * 100)

    kept = typos_plugin._text_files(
        files=["notes.txt", "photo.jpg", "doc.pdf"],
        cwd=str(tmp_path),
    )

    assert_that(kept).is_equal_to(["notes.txt"])
    assert_that(BINARY_PATH_SUFFIXES).contains(".jpg", ".pdf", ".png")


def test_manifest_pins_the_crate_not_the_binary_name() -> None:
    """The binary is ``typos`` but the crate/formula is ``typos-cli``.

    Installing ``typos`` from crates.io fetches an unrelated library, so the
    package override must stay explicit.
    """
    registry = ManifestRegistry.load()
    entry = registry.get("typos")

    assert_that(entry).is_not_none()
    assert_that(entry.install_type).is_equal_to("cargo")
    assert_that(entry.install_package).is_equal_to("typos-cli")
    assert_that(ecosystem_package_name("typos", entry.install_package)).is_equal_to(
        "typos-cli",
    )


def test_install_hint_names_the_crate_and_the_formula() -> None:
    """The hint must not tell users to install the wrong package."""
    hint = get_install_hints()["typos"]

    assert_that(hint).contains("cargo install typos-cli")
    assert_that(hint).contains("brew install typos-cli")


def test_version_command_targets_the_binary() -> None:
    """``--version`` is invoked on the binary name, not the crate name."""
    registry = ManifestRegistry.load()

    assert_that(list(registry.get("typos").version_command)).is_equal_to(
        ["typos", "--version"],
    )


def test_typos_is_absent_from_language_map_and_recommended_allowlist() -> None:
    """Init's recommended profile cannot pull typos in via language detection.

    typos is language-agnostic: it is not listed in ``language_map``, so
    ``tools_for_profile("recommended", ...)`` never adds it. That is why
    ``lintro init --profile recommended`` does not start spell-checking on
    upgrade, while an unscoped config still can.
    """
    registry = ManifestRegistry.load()
    mapped = {name for tools in registry.language_map.values() for name in tools}

    assert_that(mapped).does_not_contain("typos")
    recommended = [
        tool.name
        for tool in registry.tools_for_profile(
            "recommended",
            detected_langs=list(registry.language_map),
        )
    ]
    assert_that(recommended).does_not_contain("typos")


def test_selection_docs_name_every_config_source_and_the_allowlist() -> None:
    """User-facing selection prose must match ``get_tools_to_run``.

    A resolved config is more than ``.lintro-config.yaml``, and
    ``execution.enabled_tools`` still filters default / ``--tools all``
    runs after language scoping is bypassed.
    """
    config_doc = Path("docs/configuration.md").read_text(encoding="utf-8")
    analysis_doc = Path("docs/tool-analysis/typos-analysis.md").read_text(
        encoding="utf-8",
    )
    section_start = config_doc.find("**When typos runs.**")
    section_end = config_doc.find("`lintro check --tools typos`", section_start)
    section = config_doc[section_start:section_end]
    # Collapse markdown wrap and blockquote prefixes so prettier line breaks
    # cannot hide the required phrases.
    collapsed = " ".join(
        line.lstrip("> ").strip() for line in section.splitlines() if line.strip()
    )

    assert_that(section_start).is_not_equal_to(-1)
    assert_that(collapsed).contains("non-empty")
    assert_that(collapsed).contains("[tool.lintro]")
    assert_that(collapsed).contains("execution.enabled_tools")
    assert_that(collapsed).contains("recommended profile")
    assert_that(collapsed).contains("does **not** include typos")
    assert_that(collapsed).contains("enabled_tools: []")
    assert_that(collapsed).contains("pyproject.toml")
    assert_that(collapsed).contains("Cargo.toml")
    assert_that(collapsed).contains("native_configs")
    assert_that(collapsed).does_not_contain(
        "typos runs as soon as the binary is on",
    )
    assert_that(analysis_doc).contains("execution.enabled_tools")
    assert_that(analysis_doc).contains("non-empty")
    assert_that(analysis_doc).contains("lintro init --profile recommended")
    assert_that(analysis_doc).contains("intentionally omitted from `native_configs`")


def test_disable_docs_do_not_claim_empty_allowlist_drops_typos() -> None:
    """An empty ``enabled_tools`` list runs the full registry, including typos.

    Creating a Lintro config solely to turn typos off would skip language
    scoping; the docs must not say that empty allowlist "minus typos".
    """
    config_doc = Path("docs/configuration.md").read_text(encoding="utf-8")
    start = config_doc.find("**Turning it off.**")
    end = config_doc.find("**Project vocabulary.**", start)
    section = config_doc[start:end]
    collapsed = " ".join(
        line.lstrip("> ").strip() for line in section.splitlines() if line.strip()
    )

    assert_that(start).is_not_equal_to(-1)
    assert_that(collapsed).contains("full unscoped registry, including typos")
    assert_that(collapsed).does_not_contain("minus typos")
    assert_that(collapsed).contains("enabled: false")


def test_getting_started_notes_first_run_native_config() -> None:
    """getting-started must mention the unmapped-tool first-run caveat.

    typos is listed with other optional installable tools, but unlike
    language-mapped ones a no-config default run only selects it when a
    native config file exists. Named ``--tools`` and unscoped configs still
    run it; ``lintro init`` writes an allowlist that omits it.
    """
    getting_started = Path("docs/getting-started.md").read_text(encoding="utf-8")
    start = getting_started.find("`typos`")
    end = getting_started.find("`vale`", start)
    collapsed = " ".join(
        line.strip() for line in getting_started[start:end].splitlines() if line.strip()
    )

    assert_that(start).is_not_equal_to(-1)
    assert_that(collapsed).contains("typos.toml")
    assert_that(collapsed).contains("no-config default")
    assert_that(collapsed).contains("--tools typos")
    assert_that(collapsed).contains("execution.enabled_tools")
    assert_that(collapsed).contains("language allowlist omits it")
    assert_that(collapsed).does_not_contain("skipped otherwise")


def test_typos_version_output_parses(typos_plugin: TyposPlugin) -> None:
    """``typos --version`` prints the crate name first; parsing must cope.

    The pinned version is read from the manifest rather than written inline so
    a Renovate bump does not have to touch this test.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    pinned = get_tool_version(ToolName.TYPOS)

    assert_that(
        extract_version_from_output(f"typos-cli {pinned}", "typos"),
    ).is_equal_to(pinned)
    assert_that(typos_plugin.definition.min_version).is_not_none()


def test_typos_enum_pin_and_install_hint_land_together() -> None:
    """Version parsing, the manifest pin, and install hints must all name typos.

    A slice that only sees ``version_parsing.py`` can look as if
    ``ToolName.TYPOS`` and the pin are missing; they live in other modules
    on this branch and must stay wired together.
    """
    assert_that(ToolName.TYPOS in TOOLS_WITH_SIMPLE_VERSION_PATTERN).is_true()
    assert_that(get_minimum_versions().get("typos")).is_not_none()
    assert_that(get_install_hints().get("typos")).contains("typos-cli")
