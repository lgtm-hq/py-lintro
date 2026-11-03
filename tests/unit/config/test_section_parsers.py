"""Mapping contracts for private review, output, deps and watch parsers."""

from collections import UserDict
from collections.abc import Callable
from typing import Any

import pytest
from assertpy import assert_that
from pydantic import BaseModel

from lintro.config.config_loader import (
    _parse_deps_config,
    _parse_output_config,
    _parse_review_config,
    _parse_watch_config,
)
from lintro.config.deps_config import DepsConfig
from lintro.config.output_config import OutputConfig
from lintro.config.review_config import ReviewConfig
from lintro.config.watch_config import WatchConfig

SECTION_PARSERS: dict[
    str,
    tuple[Callable[[dict[str, Any]], BaseModel], type[BaseModel]],
] = {
    "review": (_parse_review_config, ReviewConfig),
    "output": (_parse_output_config, OutputConfig),
    "deps": (_parse_deps_config, DepsConfig),
    "watch": (_parse_watch_config, WatchConfig),
}


@pytest.mark.parametrize("section", SECTION_PARSERS)
def test_private_section_parser_empty_mapping_uses_defaults(section: str) -> None:
    """An empty mapping preserves every field's model default.

    Args:
        section: Private parser and model whose defaults must be retained.
    """
    parser, model = SECTION_PARSERS[section]

    assert_that(parser({})).is_equal_to(model())


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("review", "depth", 2),
        ("output", "art", False),
        ("deps", "require_upper_bound", False),
        ("watch", "debounce_ms", 0),
    ],
)
def test_private_section_parser_keeps_configured_value(
    section: str,
    field: str,
    value: Any,
) -> None:
    """A populated mapping retains its configured value through the guard.

    Args:
        section: Private parser that receives the mapping.
        field: Configured field on the returned model.
        value: Non-default value the parser must preserve.
    """
    parser, _ = SECTION_PARSERS[section]

    assert_that(getattr(parser({field: value}), field)).is_equal_to(value)


@pytest.mark.parametrize("section", SECTION_PARSERS)
@pytest.mark.parametrize(
    ("value", "expected_type"),
    [
        pytest.param(None, "null", id="null"),
        pytest.param(False, "bool", id="false"),
        pytest.param(0, "int", id="zero"),
        pytest.param("", "str", id="empty-string"),
        pytest.param([], "list", id="empty-list"),
        pytest.param((), "tuple", id="tuple"),
        pytest.param("invalid", "str", id="string"),
        pytest.param(["ruff"], "list", id="list"),
        pytest.param(UserDict(), "UserDict", id="non-dict-mapping"),
    ],
)
def test_private_section_parser_rejects_non_mapping(
    section: str,
    value: Any,
    expected_type: str,
) -> None:
    """Null and other non-mappings fail at the direct parser boundary.

    Inputs deliberately violate the dict annotation to exercise runtime
    validation for untyped callers. Non-dict mappings remain unsupported;
    direct parser labels retain ``config`` unlike public section labels.

    Args:
        section: Private parser expected to reject the value.
        value: Invalid section value, including falsey non-mappings.
        expected_type: Type label required in the error message.
    """
    parser, _ = SECTION_PARSERS[section]

    with pytest.raises(ValueError) as error:
        parser(value)

    assert_that(str(error.value)).is_equal_to(
        f"{section} config must be a mapping, got {expected_type}",
    )
