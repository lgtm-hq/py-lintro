"""A small, well-formed calculator module used as a benchmark fixture."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Calculator:
    """A trivial calculator over floating-point numbers.

    Attributes:
        precision: Number of decimal places used when rounding results.
    """

    precision: int = 2

    def add(self, left: float, right: float) -> float:
        """Add two numbers.

        Args:
            left: Left operand.
            right: Right operand.

        Returns:
            float: The rounded sum.
        """
        return round(left + right, self.precision)

    def subtract(self, left: float, right: float) -> float:
        """Subtract one number from another.

        Args:
            left: Left operand.
            right: Right operand.

        Returns:
            float: The rounded difference.
        """
        return round(left - right, self.precision)

    def multiply(self, left: float, right: float) -> float:
        """Multiply two numbers.

        Args:
            left: Left operand.
            right: Right operand.

        Returns:
            float: The rounded product.
        """
        return round(left * right, self.precision)

    def divide(self, left: float, right: float) -> float:
        """Divide one number by another.

        Args:
            left: Numerator.
            right: Denominator.

        Returns:
            float: The rounded quotient.

        Raises:
            ZeroDivisionError: If ``right`` is zero.
        """
        if right == 0:
            raise ZeroDivisionError("cannot divide by zero")
        return round(left / right, self.precision)
