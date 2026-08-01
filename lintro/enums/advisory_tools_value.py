"""Special values accepted by ``lintro review --advisory-tools``.

Mirrors :class:`lintro.enums.tools_value.ToolsValue` for the advisory tool
selector, which additionally understands ``none`` (run no advisory tools).
"""

from __future__ import annotations

from enum import StrEnum, auto


class AdvisoryToolsValue(StrEnum):
    """Special ``--advisory-tools`` values.

    Attributes:
        ALL: Run every advisory tool enabled in configuration (the default).
        NONE: Run no advisory tools at all.
    """

    ALL = auto()
    NONE = auto()
