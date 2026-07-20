"""Output configuration model."""

from pydantic import BaseModel, ConfigDict


class OutputConfig(BaseModel):
    """Console output presentation settings.

    Controls purely cosmetic aspects of the console output that do not affect
    the machine-readable result documents (JSON/SARIF) or on-disk artifacts.

    Attributes:
        model_config: Pydantic model configuration.
        art: Whether decorative ASCII art may be printed after a run. Even
            when ``True`` the art is only emitted to an interactive TTY and is
            never written to ``report.md`` or any ``--output-format`` stream.
            Set to ``False`` (or pass ``--no-art``) to suppress it entirely.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    art: bool = True
