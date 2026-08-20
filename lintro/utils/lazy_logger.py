"""Lazy loguru proxy for cold-start sensitive import paths.

Importing ``loguru`` costs ~20-25ms. Modules on the ``lintro --version`` /
``import lintro.cli`` path should import ``logger`` from here instead of
``from loguru import logger`` so loguru loads only on first log call.
"""

from __future__ import annotations

import threading
from typing import Any, cast


class _LazyLogger:
    """Proxy that imports loguru on first attribute access."""

    _logger: Any | None = None
    #: Guards the check-then-set of ``_logger`` so free-threaded builds (no GIL)
    #: cannot race two concurrent loguru imports into the class attribute.
    _lock: threading.Lock = threading.Lock()

    def _get_logger(self) -> Any:
        """Return the real loguru logger, importing it on first use.

        Returns:
            The loguru logger instance.
        """
        cached = type(self)._logger
        if cached is not None:
            return cast(Any, cached)
        with type(self)._lock:
            if type(self)._logger is None:
                from loguru import logger as real_logger

                type(self)._logger = real_logger
            return cast(Any, type(self)._logger)

    def __getattr__(self, name: str) -> Any:
        """Forward attribute access to the real loguru logger.

        Args:
            name: Attribute name requested by the caller.

        Returns:
            The attribute from the real loguru logger.
        """
        return getattr(self._get_logger(), name)

    def __bool__(self) -> bool:
        """Treat the proxy as truthy like the real logger.

        Returns:
            Always True.
        """
        return True


logger = _LazyLogger()

__all__ = ["logger"]
