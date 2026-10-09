"""Windows: put photag in the "Open with" menu of pictures and in Settings > Apps > Default apps (current user only, no administrator).

photag.exe "<picture>" then shows the picture in photag's viewer (app/viewer.py) without adding it to the catalog. Windows does not let a
program make itself the default: the user picks photag for a picture type in Default apps (open_default_apps()), once.

    HKCU\\Software\\Classes\\photag.Image\\shell\\open\\command          "<exe>" "%1"
    HKCU\\Software\\Classes\\<.jpg ...>\\OpenWithProgids   photag.Image  (the "Open with" list)
    HKCU\\Software\\Classes\\Applications\\<exe name>\\...              (the "Open with > Choose another app" entry)
    HKCU\\Software\\photag\\Capabilities + HKCU\\Software\\RegisteredApplications   (Default apps)

The installer (installer.iss, task "openwith") writes the same keys; tests point the roots elsewhere (classes= / software=).
"""
import os
import sys
from pathlib import Path

PROGID = "photag.Image"
EXTS = [".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif",
        ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2",
        ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".wmv", ".mpg", ".mpeg", ".3gp"]          # videos too: the viewer plays them
CLASSES = r"Software\Classes"
SOFTWARE = r"Software"


def exe() -> str | None:
    env = os.environ.get("PHOTAG_FILEASSOC_EXE")            # tests
    if env:
        return env
    return sys.executable if getattr(sys, "frozen", False) else None


def supported() -> bool:
    return sys.platform == "win32" and bool(exe())


def plan(exe_path: str, classes: str = CLASSES, software: str = SOFTWARE) -> list[tuple[str, str, str, str]]:
    """Every registry value to write: (key under HKCU, value name ('' = the default value), data, 'sz' | 'none')."""
    name = Path(exe_path).name
    cmd = f'"{exe_path}" "%1"'
    rows = [
        (f"{classes}\\{PROGID}", "", "Picture", "sz"),
        (f"{classes}\\{PROGID}\\DefaultIcon", "", f'"{exe_path}",0', "sz"),
        (f"{classes}\\{PROGID}\\shell\\open", "FriendlyAppName", "photag", "sz"),
        (f"{classes}\\{PROGID}\\shell\\open\\command", "", cmd, "sz"),
        (f"{classes}\\Applications\\{name}", "FriendlyAppName", "photag", "sz"),
        (f"{classes}\\Applications\\{name}\\shell\\open", "FriendlyAppName", "photag", "sz"),
        (f"{classes}\\Applications\\{name}\\shell\\open\\command", "", cmd, "sz"),
        (f"{software}\\photag\\Capabilities", "ApplicationName", "photag", "sz"),
        (f"{software}\\photag\\Capabilities", "ApplicationDescription", "Photo manager and picture viewer: shows a picture without adding it to the catalog", "sz"),
        (f"{software}\\RegisteredApplications", "photag", f"{software}\\photag\\Capabilities", "sz"),
    ]
    for e in EXTS:
        rows += [(f"{classes}\\{e}\\OpenWithProgids", PROGID, "", "none"),
                 (f"{classes}\\Applications\\{name}\\SupportedTypes", e, "", "sz"),
                 (f"{software}\\photag\\Capabilities\\FileAssociations", e, PROGID, "sz")]
    return rows


def _roots(classes: str, software: str):
    """Keys that are removed as a whole when switching off (the OpenWithProgids values are removed one by one)."""
    return [f"{classes}\\{PROGID}", f"{software}\\photag"]


def register(exe_path: str | None = None, classes: str = CLASSES, software: str = SOFTWARE) -> None:
    import winreg
    p = exe_path or exe()
    if not p:
        raise RuntimeError("photag.exe not known")
    for key, name, data, kind in plan(p, classes, software):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ if kind == "sz" else winreg.REG_NONE, data if kind == "sz" else b"")
    _notify()


def _delete_tree(key: str):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(f"{key}\\{sub}")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    except FileNotFoundError:
        pass


def unregister(exe_path: str | None = None, classes: str = CLASSES, software: str = SOFTWARE) -> None:
    import winreg
    p = exe_path or exe() or "photag.exe"
    name = Path(p).name
    for e in EXTS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{classes}\\{e}\\OpenWithProgids", 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, PROGID)
        except (FileNotFoundError, OSError):
            pass
    for key in _roots(classes, software) + [f"{classes}\\Applications\\{name}"]:
        _delete_tree(key)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{software}\\RegisteredApplications", 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, "photag")
    except (FileNotFoundError, OSError):
        pass
    _notify()


def _off_marker() -> Path:
    from . import platform_dirs
    return platform_dirs.roaming_base() / "photag" / "no-open-with"


def opted_out() -> bool:
    """The user switched "Open with" off in Preferences: photag does not put itself back."""
    return _off_marker().exists()


def set_opted_out(off: bool) -> None:
    m = _off_marker()
    try:
        if off:
            m.parent.mkdir(parents=True, exist_ok=True)
            m.write_text("", "utf-8")
        elif m.exists():
            m.unlink()
    except OSError:
        pass


def _offered_marker() -> Path:
    from . import platform_dirs
    return platform_dirs.roaming_base() / "photag" / "viewer-offered"


def offered() -> bool:
    """The one-time "make photag your picture viewer?" question was shown already (kept next to the data, not in the browser storage,
    which is lost when the page's address changes)."""
    return _offered_marker().exists()


def set_offered() -> None:
    m = _offered_marker()
    try:
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text("", "utf-8")
    except OSError:
        pass


def is_default() -> bool:
    """True when Windows already opens pictures (.jpg or .png) with photag (the user's choice in Default apps)."""
    if sys.platform != "win32":
        return False
    import winreg
    for e in (".jpg", ".png"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{e}\UserChoice") as k:
                if "photag" in str(winreg.QueryValueEx(k, "ProgId")[0]).lower():
                    return True
        except (FileNotFoundError, OSError):
            pass
    return False


def ensure() -> bool:
    """On by default: when photag starts (installed or portable, or after a code update) and is not yet in the "Open with" menu
    for this photag.exe, put it there -- unless the user switched it off. Returns True when it registered."""
    if not supported() or opted_out() or is_registered():
        return False
    try:
        register()
        return True
    except OSError:
        return False


def is_registered(exe_path: str | None = None, classes: str = CLASSES) -> bool:
    """True when the "Open with" entry exists and points at this photag.exe."""
    import winreg
    p = exe_path or exe()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{classes}\\{PROGID}\\shell\\open\\command") as k:
            return winreg.QueryValueEx(k, "")[0] == f'"{p}" "%1"'
    except (FileNotFoundError, OSError):
        return False


def _notify():
    """Tell Explorer that file associations changed (SHCNE_ASSOCCHANGED), so the menus update without a restart."""
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass


def open_default_apps():
    """Open Windows Settings on photag's page of Default apps (needs the registration above)."""
    try:
        os.startfile("ms-settings:defaultapps?registeredAppUser=photag")      # type: ignore[attr-defined]
    except OSError:
        os.startfile("ms-settings:defaultapps")                                # type: ignore[attr-defined]
