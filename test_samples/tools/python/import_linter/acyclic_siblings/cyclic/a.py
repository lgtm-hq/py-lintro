"""Sibling a: imports sibling b, closing a cycle."""

from cyclic import b

__all__ = ["b"]
