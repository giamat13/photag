"""Paths & settings. Library location is stored in a tiny pointer file in
%APPDATA%\\photag so the app always knows (and can tell you) where your
photos live, independent of where the EXE runs from."""
import json
import os
import shutil
from pathlib import Path

APP_NAME = "photag"
OLD_APP_NAME = "PhotoManager"  # the app's name before the rename; its folders keep working

def _appdata_dir() -> Path:
    base = Path(os.environ.get("APPDATA") or os.path.expanduser("~"))
    d = base / APP_NAME
    old = base / OLD_APP_NAME
    if not (d / "config.json").exists() and (old / "config.json").exists():
        d.mkdir(parents=True, exist_ok=True)  # carry the pointer over so the existing catalog is found
        shutil.copy2(old / "config.json", d / "config.json")
    d.mkdir(parents=True, exist_ok=True)
    return d

_POINTER = _appdata_dir() / "config.json"
_HOME = Path(os.path.expanduser("~"))
# An install from before the rename keeps its photos where they already are.
_DEFAULT_LIBRARY = (_HOME / OLD_APP_NAME if (_HOME / OLD_APP_NAME / "catalog.db").exists()
                    and not (_HOME / APP_NAME).exists() else _HOME / APP_NAME)


def _read_pointer() -> dict:
    if _POINTER.exists():
        try:
            return json.loads(_POINTER.read_text("utf-8"))
        except Exception:
            pass
    return {}


def _write_pointer(**updates) -> None:
    """Merge into the pointer file rather than overwrite it -> unrelated
    settings don't clobber each other."""
    data = _read_pointer()
    for k, v in updates.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    _POINTER.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def get_library_root() -> Path:
    p = _read_pointer().get("library_root")
    return Path(p) if p else _DEFAULT_LIBRARY


def get_ai() -> dict:
    """AI tagging settings: provider, model, language, base_url and the encrypted per-provider keys."""
    return _read_pointer().get("ai") or {}


def set_ai(data: dict) -> None:
    _write_pointer(ai=data or None)


def get_update_skipped() -> str | None:
    """The release version the user chose to skip ("Skip this version")."""
    return _read_pointer().get("update_skipped") or None


def set_update_skipped(version: str | None) -> None:
    _write_pointer(update_skipped=version or None)


def settings_dir() -> Path:
    """The folder of the settings file (%APPDATA%\\photag)."""
    return _POINTER.parent


def read_all() -> dict:
    """Every saved setting (for backups)."""
    return dict(_read_pointer())


def merge_settings(updates: dict) -> None:
    _write_pointer(**updates)


def get_backup() -> dict:
    """Backup settings: enabled, interval_hours, keep, include_media, folder."""
    return _read_pointer().get("backup") or {}


def set_backup(data: dict) -> None:
    _write_pointer(backup=data or None)


def get_handbrake_path() -> str | None:
    """User-chosen HandBrakeCLI location (when it's not on PATH or in a standard folder)."""
    return _read_pointer().get("handbrake_path") or None


def set_handbrake_path(path: str | None) -> None:
    _write_pointer(handbrake_path=path or None)


def set_library_root(path: str | os.PathLike) -> Path:
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    _write_pointer(library_root=str(root))
    return root


class Paths:
    """Resolved once per process; call refresh() after changing the library root."""
    def __init__(self):
        self.refresh()

    def refresh(self):
        self.root = get_library_root()
        self.media = self.root / "media"
        self.thumbs = self.root / "thumbs"
        self.db = self.root / "catalog.db"
        for d in (self.root, self.media, self.thumbs):
            d.mkdir(parents=True, exist_ok=True)
        return self


PATHS = Paths()

# Face clustering: cosine distance, scipy average-linkage, cut at this height.
FACE_CLUSTER_THRESHOLD = 0.38
FACE_MODEL = "buffalo_l"
# Photos sit in the trash this many days before being deleted for good.
TRASH_RETENTION_DAYS = 60
