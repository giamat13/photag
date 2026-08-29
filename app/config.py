"""Paths & settings. Library location is stored in a tiny pointer file in
%APPDATA%\\PhotoManager so the app always knows (and can tell you) where your
photos live, independent of where the EXE runs from."""
import json
import os
from pathlib import Path

APP_NAME = "PhotoManager"

def _appdata_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d

_POINTER = _appdata_dir() / "config.json"
_DEFAULT_LIBRARY = Path(os.path.expanduser("~")) / APP_NAME


def get_library_root() -> Path:
    if _POINTER.exists():
        try:
            p = json.loads(_POINTER.read_text("utf-8")).get("library_root")
            if p:
                return Path(p)
        except Exception:
            pass
    return _DEFAULT_LIBRARY


def set_library_root(path: str | os.PathLike) -> Path:
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    _POINTER.write_text(json.dumps({"library_root": str(root)}, ensure_ascii=False, indent=2), "utf-8")
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
# Ollama vision model for content tags (optional; skipped if Ollama is down).
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "llava")

# Photos sit in the trash this many days before being deleted for good.
TRASH_RETENTION_DAYS = 60
