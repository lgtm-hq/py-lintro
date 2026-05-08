"""Custom exceptions for the tool-version generator."""

from __future__ import annotations


class GenerationError(Exception):
    """Raised on unrecoverable input errors.

    The CLI converts this to ``EXIT_INPUT_ERROR`` (2). Re-raise to higher
    layers verbatim; the message is the user-facing diagnostic.
    """
