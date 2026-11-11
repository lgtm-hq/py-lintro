"""Sibling b: imports sibling a, closing a cycle."""

from cyclic import a

__all__ = ["a"]
