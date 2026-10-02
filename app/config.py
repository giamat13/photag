"""Paths & settings. Library location is stored in a tiny pointer file in
%APPDATA%\\photag so the app always knows (and can tell you) where your
photos live, independent of where the EXE runs from.

Portable mode (tools/make_portable.py): a ZIP of the program folder with no installer, meant to run from a USB stick or
any folder, possibly on a different PC each time. A file named "portable.txt" next to photag.exe switches both the
settings pointer and the default library into a "data" folder beside the EXE, so nothing is written to this PC's
%APPDATA% or user profile and the whole thing stays self-contained on the drive it runs from."""
import json
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "photag"
OLD_APP_NAME = "PhotoManager"  # the app's name before the rename; its folders keep working


def portable_dir() -> Path | None:
    """The folder next to the EXE, if this is a portable build (a 'portable.txt' marker sits there); else None."""
    env = os.environ.get("PHOTAG_PORTABLE_DIR")           # tests
    if env:
        return Path(env)
    if not getattr(sys, "frozen", False):
        return None
    base = Path(sys.executable).parent
    return base if (base / "portable.txt").is_file() else None


PORTABLE = portable_dir()


def _appdata_dir() -> Path:
    if PORTABLE:
        d = PORTABLE / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d
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
TARGET_LIBRARY = _HOME / "Photag"            # where new libraries go: C:\Users\<you>\Photag
LEGACY_LIBRARY = _HOME / OLD_APP_NAME        # C:\Users\<you>\PhotoManager, the folder of installs from before the rename


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return str(a).rstrip("\\/").lower() == str(b).rstrip("\\/").lower()


def _default_library() -> Path:
    """New installs use ~/Photag. An existing PhotoManager library keeps being used (nothing is lost or hidden)
    until the user agrees to move it, see request_legacy_move(). A portable build defaults to a folder beside the EXE
    instead, so the library travels with the program on its drive."""
    if PORTABLE:
        return PORTABLE / "data" / "library"
    if (LEGACY_LIBRARY / "catalog.db").exists() and not (TARGET_LIBRARY / "catalog.db").exists():
        return LEGACY_LIBRARY
    return TARGET_LIBRARY


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
    if not p:
        return _default_library()
    if PORTABLE and p.startswith("portable:"):
        return PORTABLE / p[len("portable:"):]            # relative to wherever the exe runs from now, not where it was saved
    return Path(p)


def legacy_library_in_use() -> str | None:
    """The old ~/PhotoManager folder, when that is the library in use and a move to ~/Photag is possible.
    Not offered in portable mode: that rename is about this PC's user profile, which a portable build does not use."""
    if PORTABLE:
        return None
    if (LEGACY_LIBRARY / "catalog.db").exists() and _same_dir(get_library_root(), LEGACY_LIBRARY) and not (TARGET_LIBRARY / "catalog.db").exists():
        return str(LEGACY_LIBRARY)
    return None


def request_legacy_move() -> None:
    """The user agreed: the folder is renamed the next time the app starts (nothing has the files open then)."""
    _write_pointer(move_legacy=True, move_error=None, moved_from=None)


def move_notice() -> dict:
    """What happened to a requested move, for the UI to report once: {"moved_from", "moved_to", "error"} (or empty)."""
    d = _read_pointer()
    out = {}
    if d.get("moved_from"):
        out["moved_from"], out["moved_to"] = d["moved_from"], d.get("library_root")
    if d.get("move_error"):
        out["error"] = d["move_error"]
    return out


def ack_move_notice() -> None:
    _write_pointer(moved_from=None, move_error=None)


def _apply_pending_move() -> None:
    """At start-up, before anything opens the catalog: rename ~/PhotoManager to ~/Photag if the user asked for it."""
    d = _read_pointer()
    if not d.get("move_legacy"):
        return
    _write_pointer(move_legacy=None)                                  # one attempt per request
    try:
        if not (LEGACY_LIBRARY / "catalog.db").exists():
            return
        cur = d.get("library_root")
        if cur and not _same_dir(Path(cur), LEGACY_LIBRARY):
            return                                                    # the library in use is somewhere else: nothing to move
        if TARGET_LIBRARY.exists():
            if any(TARGET_LIBRARY.iterdir()):
                raise OSError(f"{TARGET_LIBRARY} already exists and is not empty")
            TARGET_LIBRARY.rmdir()                                    # an empty leftover folder
        os.replace(LEGACY_LIBRARY, TARGET_LIBRARY)                    # a rename on the same drive: instant, nothing is copied
        _write_pointer(library_root=str(TARGET_LIBRARY), moved_from=str(LEGACY_LIBRARY))
    except OSError as e:
        _write_pointer(move_error=str(e)[:300])


if not os.environ.get("PHOTAG_BACKGROUND"):     # the background backup must never rename the folder under a running app
    _apply_pending_move()


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


def get_auto_import() -> dict:
    """Automatic import from a folder: enabled, folder."""
    return _read_pointer().get("auto_import") or {}


def set_auto_import(data: dict) -> None:
    _write_pointer(auto_import=data or None)


def get_handbrake_path() -> str | None:
    """User-chosen HandBrakeCLI location (when it's not on PATH or in a standard folder)."""
    return _read_pointer().get("handbrake_path") or None


def set_handbrake_path(path: str | None) -> None:
    _write_pointer(handbrake_path=path or None)


def set_library_root(path: str | os.PathLike) -> Path:
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if PORTABLE:
        try:
            rel = root.relative_to(PORTABLE.resolve())    # PORTABLE resolved too: Windows can report the same folder
            _write_pointer(library_root="portable:" + str(rel))   # under a short (8.3) name in one place and the long name in another
            return root
        except ValueError:
            pass                                                  # a path outside the portable folder: the user's deliberate choice, kept as-is
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
# A little looser than FACE_CLUSTER_THRESHOLD: for suggesting "is this <name>?" on an unnamed
# group that is close to -- but not quite tight enough to have merged with -- an already-named
# person (different lighting/angle/session). Wrong suggestions are one click away from being
# ignored, so a slightly loose threshold trades a few misses for far fewer missed suggestions.
FACE_SUGGEST_THRESHOLD = 0.46
FACE_MODEL = "buffalo_l"
# Photos sit in the trash this many days before being deleted for good.
TRASH_RETENTION_DAYS = 60
