"""Load and save the two folder settings as JSON."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "FileSync"
SETTINGS_PATH = SUPPORT_DIR / "settings.json"


@dataclass(frozen=True)
class Settings:
    source: str = ""
    destination: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.source and self.destination)


def settings_to_json(settings: Settings) -> str:
    return json.dumps(asdict(settings), indent=2) + "\n"


def settings_from_json(text: str) -> Settings:
    """Parse settings, ignoring unknown keys. Raises ValueError on malformed input."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Settings must be a JSON object.")
    return Settings(source=str(data.get("source") or ""), destination=str(data.get("destination") or ""))


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    """Return saved settings, or empty settings if none exist or the file is unreadable."""
    try:
        return settings_from_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()


def save_settings(settings: Settings, path: Path = SETTINGS_PATH) -> None:
    """Write settings atomically so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(settings_to_json(settings))
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
