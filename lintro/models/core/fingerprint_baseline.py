"""Strict, versioned models for explicit finding acknowledgements."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BaselineFinding(Protocol):
    """The diagnostic surface needed for identity, independent of parsers."""

    file: str
    line: int
    message: str

    def get_code(self) -> str:
        """Return the producer's canonical rule identifier."""
        ...


class BaselineEntry(BaseModel):
    """One fingerprint and its acknowledged occurrence budget."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    tool: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    file: str = Field(min_length=1, max_length=4096)
    code: str = Field(max_length=1024)
    message_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    count: int = Field(ge=1, le=1_000_000)
    line: int = Field(ge=0, le=2_147_483_647)

    @field_validator("file")
    @classmethod
    def canonical_file(cls, value: str) -> str:
        """Reject paths that cannot denote a canonical workspace-relative file.

        Args:
            value: Untrusted POSIX-style path.

        Returns:
            The validated path.

        Raises:
            ValueError: If the path is absolute, noncanonical, or traverses.
        """
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or PureWindowsPath(value).drive
            or "\\" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or path.as_posix() != value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("baseline file paths must be canonical relative paths")
        return value

    @property
    def key(self) -> tuple[str, str, str, str]:
        """Return the identity, excluding the occurrence budget and line hint.

        Returns:
            Tool, file, rule, and message digest.
        """
        return self.tool, self.file, self.code, self.message_hash


class FingerprintBaseline(BaseModel):
    """Portable acknowledgement file with a pinned matching algorithm."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    version: Literal[1]
    created: str
    entries: list[BaselineEntry] = Field(max_length=100_000)

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        """Reject booleans and floats that compare equal to version one.

        Args:
            value: Untrusted version.

        Returns:
            The integer version.

        Raises:
            ValueError: If the version is not an exact integer.
        """
        if type(value) is not int:
            raise ValueError("baseline version must be an integer")
        return value

    @field_validator("created")
    @classmethod
    def utc_timestamp(cls, value: str) -> str:
        """Validate the creation timestamp without using it for identity.

        Args:
            value: UTC timestamp.

        Returns:
            The validated timestamp.

        Raises:
            ValueError: If the timestamp is invalid.
        """
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
            raise ValueError("baseline created must be a UTC timestamp")
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return value

    @model_validator(mode="after")
    def unique_entries(self) -> FingerprintBaseline:
        """Reject duplicate identities rather than silently enlarging budgets.

        Returns:
            The validated baseline.

        Raises:
            ValueError: If any fingerprint occurs twice.
        """
        if len({entry.key for entry in self.entries}) != len(self.entries):
            raise ValueError("duplicate baseline fingerprint")
        return self
