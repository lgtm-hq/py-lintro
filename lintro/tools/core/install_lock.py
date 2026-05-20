"""Serialize and deserialize install lock files for reproducible tool installs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class InstallLockEntry:
    """Resolved tool entry for a lock file."""

    name: str
    version: str
    install_hint: str = ""
    profile: str | None = None


@dataclass
class InstallLock:
    """Lock file describing a resolved install plan."""

    schema_version: int = 1
    created_at: str = field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat(),
    )
    profile: str | None = None
    detected_languages: list[str] = field(default_factory=list)
    tools: list[InstallLockEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstallLock:
        """Parse lock data from a dict."""
        raw_tools = data.get("tools", [])
        tools: list[InstallLockEntry] = []
        if isinstance(raw_tools, list):
            for entry in raw_tools:
                if isinstance(entry, dict) and entry.get("name"):
                    tools.append(
                        InstallLockEntry(
                            name=str(entry["name"]),
                            version=str(entry.get("version", "")),
                            install_hint=str(entry.get("install_hint", "")),
                            profile=entry.get("profile"),
                        ),
                    )
        langs = data.get("detected_languages", [])
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            created_at=str(data.get("created_at", "")),
            profile=data.get("profile"),
            detected_languages=(
                [str(x) for x in langs] if isinstance(langs, list) else []
            ),
            tools=tools,
        )


def write_install_lock(path: Path, lock: InstallLock) -> None:
    """Write lock file as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError:
            path = path.with_suffix(".json")
            path.write_text(
                json.dumps(lock.to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
            return
        path.write_text(yaml.safe_dump(lock.to_dict()), encoding="utf-8")
    else:
        out = path if path.suffix == ".json" else path.with_suffix(".json")
        out.write_text(json.dumps(lock.to_dict(), indent=2) + "\n", encoding="utf-8")


def read_install_lock(path: Path) -> InstallLock:
    """Read lock file from JSON or YAML."""
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError(
                "PyYAML required to read YAML lock files; use .json instead",
            ) from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Install lock root must be an object")
    return InstallLock.from_dict(data)
