"""Where photag keeps its per-user files, per operating system (standard library only).

Windows keeps exactly what it always had (%APPDATA% / %LOCALAPPDATA%, falling back to the home folder);
macOS uses ~/Library/Application Support, Linux the XDG folders. The environment variables APPDATA / LOCALAPPDATA
still win everywhere when set (the tests and the portable build rely on that)."""
import os
import sys
from pathlib import Path


def roaming_base() -> Path:
    """The folder that holds settings (the old %APPDATA%)."""
    env = os.environ.get("APPDATA")
    if env:
        return Path(env)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    if sys.platform != "win32":
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return Path.home()


def local_base() -> Path:
    """The folder for big or machine-local files: update journal, models, tools (the old %LOCALAPPDATA%)."""
    env = os.environ.get("LOCALAPPDATA")
    if env:
        return Path(env)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    if sys.platform != "win32":
        return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return Path.home()
