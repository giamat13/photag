"""Background mode (Windows): photag keeps running when its window is closed, with an icon next to the clock.

While photag is open, an icon sits in the notification area. Closing the window then does NOT end the program: the server (automatic
import, the backup, ...) keeps working, and the icon's menu has "Open photag", "Back up now" and "Exit". "Exit" really ends it. Preferences
has a switch for all of this ("keep"; on by default) and one for starting photag in the background when you sign in ("autostart":
`photag.exe --background`, no window).

It works for programs that already have an older launcher (photag.py is inside the exe and cannot be updated by a code update): the
program is held open by an atexit handler registered here, and `--background` is turned into PHOTAG_NO_WINDOW, which every launcher knows.

The same module watches the backup (alerts()): "the backup drive is not connected", "no backup for N days", "the backup failed" become a
notification from the icon (or, for the scheduled background backup that has no window, from a short-lived icon: tray.balloon).
Each problem is announced once every 12 hours, not more.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import backup, config, fileassoc, tray

KEY = "background"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "photag-background"
ALERT_EVERY = 12 * 3600            # the same problem is announced again after this long, if it is still there
CHECK_EVERY = 15 * 60

# `photag.exe --background` (sign-in) must not open a window: the launcher already knows this switch (it is for tests of the packaged program)
if "--background" in sys.argv:
    os.environ.setdefault("PHOTAG_NO_WINDOW", "1")

_S = {"tray": None, "quit": threading.Event(), "started": False}


# ---------------------------------------------------------------- settings
def get() -> dict:
    s = config.read_all().get(KEY) or {}
    return {"keep": bool(s.get("keep", True)), "autostart": bool(s.get("autostart", False))}


def set_(keep: bool | None = None, autostart: bool | None = None) -> dict:
    cur = get()
    if keep is not None:
        cur["keep"] = bool(keep)
    if autostart is not None:
        cur["autostart"] = bool(autostart)
    if cur["autostart"]:
        cur["keep"] = True                      # starting in the background only makes sense with the icon
    config.merge_settings({KEY: cur})
    return cur


def supported() -> bool:
    return tray.supported() and bool(fileassoc.exe())


def set_ui_lang(code: str) -> None:
    if isinstance(code, str) and 2 <= len(code) <= 3 and code.isalpha() and code.islower():
        config.merge_settings({"ui_lang": code})


def tr(text: str, vars: dict | None = None) -> str:
    """The text in the language the user chose in the window (the program asks the server to remember it); English when unknown."""
    lang = config.read_all().get("ui_lang") or "en"
    s = text
    if lang != "en":
        try:
            d = json.loads((Path(__file__).parent / "ui" / "locales" / f"{lang}.json").read_text("utf-8"))
            s = d.get(text) or text
        except (OSError, ValueError):
            pass
    try:
        return s.format(**vars) if vars else s
    except (KeyError, IndexError, ValueError):
        return text.format(**vars) if vars else text


# ---------------------------------------------------------------- start at sign-in
def run_command(exe_path: str) -> str:
    return f'"{exe_path}" --background'


def autostart_on(exe_path: str | None = None, run_key: str = RUN_KEY) -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as k:
            return winreg.QueryValueEx(k, RUN_NAME)[0] == run_command(exe_path or fileassoc.exe() or "")
    except OSError:
        return False


def set_autostart_entry(on: bool, exe_path: str | None = None, run_key: str = RUN_KEY) -> None:
    import winreg
    p = exe_path or fileassoc.exe()
    if on:
        if not p:
            raise RuntimeError("photag.exe not known")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_key) as k:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, run_command(p))
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, RUN_NAME)
        except OSError:
            pass


# ---------------------------------------------------------------- backup alerts
def pending_alerts(now: float | None = None) -> list[dict]:
    """What is wrong with the backup right now: [{"key", "title", "text", "warning"}] (most important first)."""
    now = now or time.time()
    h = backup.health(now)
    if not h["enabled"]:
        return []
    out = []
    last = h.get("last_success")
    if h.get("folder_error"):
        out.append({"key": "drive", "title": tr("The backup drive is not connected", {}),
                    "text": tr("Connect the drive so that photag can back up your photos.", {}), "warning": True})
    elif h.get("overdue") and last:
        n = max(2, int((now - last) // 86400))
        out.append({"key": "overdue", "title": tr("No backup for {n} days", {"n": n}),
                    "text": tr("The last backup was on {date}. Open photag to see why.", {"date": time.strftime("%Y-%m-%d", time.localtime(last))}), "warning": True})
    elif h.get("last_error") and int(h.get("fail_count") or 0) >= 2:
        out.append({"key": "failed", "title": tr("The backup failed", {}),
                    "text": tr("photag could not finish the last backups. Open photag to see why.", {}), "warning": True})
    return out


def _alert_file() -> Path:
    return config.settings_dir() / "alerts.json"


def _read_alerts() -> dict:
    try:
        return json.loads(_alert_file().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def due_alerts(now: float | None = None) -> list[dict]:
    """pending_alerts() minus those announced less than 12 hours ago; remembers what it returns. A problem that went away is forgotten,
    so it is announced again when it comes back."""
    now = now or time.time()
    pend = pending_alerts(now)
    seen = _read_alerts()
    keep = {a["key"]: seen[a["key"]] for a in pend if a["key"] in seen}
    out = []
    for a in pend:
        if now - float(keep.get(a["key"], 0)) >= ALERT_EVERY:
            out.append(a)
            keep[a["key"]] = now
    try:
        _alert_file().parent.mkdir(parents=True, exist_ok=True)
        _alert_file().write_text(json.dumps(keep), "utf-8")
    except OSError:
        pass
    return out


def announce_headless() -> bool:
    """For the scheduled background backup (no window, no icon): one notification from a short-lived icon if something is wrong."""
    try:
        if not tray.supported():
            return False
        due = due_alerts()
        if not due:
            return False
        a = due[0]
        return tray.balloon(a["title"], a["text"], a["warning"], icon_path=_icon_path())
    except Exception:
        return False


# ---------------------------------------------------------------- the icon
def _icon_path() -> str | None:
    p = Path(__file__).parent / "ui" / "icon.ico"
    return str(p) if p.is_file() else None


def _open_window():
    exe = fileassoc.exe()
    if not exe:
        return
    env = {k: v for k, v in os.environ.items() if k != "PHOTAG_NO_WINDOW"}
    subprocess.Popen([exe], env=env, close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def _backup_now():
    t = _S["tray"]

    def run():
        try:
            if t:
                t.notify(tr("Backup started", {}), tr("You can keep working; photag tells you when it is done.", {}))
            backup.create_tracked("manual", "tray")
            if t:
                t.notify(tr("Backup finished", {}), "")
        except backup.BusyError:
            if t:
                t.notify(tr("A backup is already running", {}), "")
        except Exception:
            if t:
                t.notify(tr("The backup failed", {}), tr("photag could not finish the last backups. Open photag to see why.", {}), True)
    threading.Thread(target=run, daemon=True).start()


def quit_now():
    """Exit for real (the icon's menu, or Windows / an installer asking the program to end)."""
    _S["quit"].set()
    try:
        if _S["tray"]:
            _S["tray"].stop(1.0)
    finally:
        os._exit(0)


def _watch():
    time.sleep(90)
    while not _S["quit"].is_set():
        try:
            t = _S["tray"]
            if t and t.alive():
                for a in due_alerts():
                    t.notify(a["title"], a["text"], a["warning"])
        except Exception:
            pass
        _S["quit"].wait(CHECK_EVERY)


def _hold():
    """atexit: the window was closed and the main program is about to end -- stay (server, icon) until "Exit" is chosen."""
    t = _S["tray"]
    if _S["quit"].is_set() or not t or not t.alive() or not get()["keep"]:
        return
    if not config.read_all().get("bg_tip_shown"):
        config.merge_settings({"bg_tip_shown": True})
        t.notify(tr("photag is still running", {}), tr("It keeps importing and backing up in the background. Use the icon next to the clock to open it or to exit.", {}))
    _S["quit"].wait()


def start() -> bool:
    """Called once when the server of this program has started. False when background mode is off or not possible."""
    if _S["started"] or not supported() or os.environ.get("PHOTAG_NO_TRAY"):
        return False
    from . import viewer
    if viewer.STARTED_WITH_PICTURE:                      # "Open with photag" on a picture: a viewer, not the program -- it ends with its window
        return False
    if not get()["keep"]:
        return False
    _S["started"] = True
    guard = config.settings_dir() / "tray-starting"
    try:
        if guard.exists():                                # the last attempt to make the icon never finished (the program died): do not try again
            guard.unlink(missing_ok=True)
            set_(keep=False, autostart=False)
            return False
        guard.parent.mkdir(parents=True, exist_ok=True)
        guard.write_text("", "utf-8")
    except OSError:
        pass
    try:
        t = tray.Tray("photag", [(tr("Open photag", {}), _open_window), (tr("Back up now", {}), _backup_now), None, (tr("Exit", {}), quit_now)],
                      _icon_path(), on_open=_open_window, on_end=quit_now)
        ok = t.start()
    except Exception:
        ok = False
    guard.unlink(missing_ok=True)
    if not ok:
        return False
    _S["tray"] = t
    __import__("atexit").register(_hold)                  # not an `import`: atexit is built into Python, nothing a code update could be missing
    threading.Thread(target=_watch, daemon=True, name="photag-alerts").start()
    return True


def apply() -> dict:
    """The settings changed while the program runs: show / remove the icon now."""
    keep = get()["keep"]
    t = _S["tray"]
    if not keep and t:
        t.stop(1.0)
        _S["tray"] = None
        _S["started"] = False
    elif keep and not (t and t.alive()):
        _S["started"] = False
        start()
    return status()


def status() -> dict:
    t = _S["tray"]
    return {"supported": supported(), **get(), "running": bool(t and t.alive())}
