"""Application resource and writable user-data locations."""

from __future__ import annotations

import os
from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parent


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Return the read-only root containing bundled assets and public songs."""
    return Path(getattr(sys, "_MEIPASS", SOURCE_ROOT)).resolve()


def user_data_root() -> Path:
    """Return a stable writable root, overridable for tests and portable runs."""
    override = os.environ.get("MIDI_PLAYGROUND_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if not is_packaged():
        return SOURCE_ROOT
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "MidiPlayground"
    return Path.home() / "AppData" / "Local" / "MidiPlayground"


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def user_path(*parts: str) -> Path:
    return user_data_root().joinpath(*parts)


def settings_path() -> Path:
    if is_packaged() or os.environ.get("MIDI_PLAYGROUND_DATA_DIR"):
        return user_path("settings.json")
    return resource_path("assets", "settings.json")
