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
    settings (library_root, vision_model, ...) don't clobber each other."""
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


def set_library_root(path: str | os.PathLike) -> Path:
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    _write_pointer(library_root=str(root))
    return root


def get_vision_model_override() -> str | None:
    """User-chosen Ollama vision model for tagging, or None for auto (largest
    installed model that fits available RAM/VRAM — see tagging._choose_model)."""
    return _read_pointer().get("vision_model") or None


def set_vision_model_override(name: str | None) -> None:
    _write_pointer(vision_model=name or None)


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
# Ollama vision model for content tags (optional; skipped if Ollama is down).
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "llava")

# Photos sit in the trash this many days before being deleted for good.
TRASH_RETENTION_DAYS = 60
