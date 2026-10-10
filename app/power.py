"""Is the computer running on its battery? Heavy work that nobody is waiting for (the automatic backup, filling in EXIF, moving old edits to the
new model, scanning your own folder) then waits until it is plugged in. Things you start yourself ("Back up now", face detection, ...) always run.

Preferences > "Wait for the charger with heavy background work" (on by default). A backup is never put off for more than 3 days.
PHOTAG_FAKE_BATTERY=1 / 0 pretends (tests).
"""
import ctypes
import os
import sys
from pathlib import Path

from . import config

KEY = "pause_on_battery"
MAX_WAIT = 3 * 86400            # an automatic backup that has not happened for this long runs even on battery


def enabled() -> bool:
    return bool(config.read_all().get(KEY, True))


def set_enabled(on: bool) -> bool:
    config.merge_settings({KEY: bool(on)})
    return bool(on)


def on_battery() -> bool:
    fake = os.environ.get("PHOTAG_FAKE_BATTERY")
    if fake is not None:
        return fake == "1"
    try:
        if sys.platform == "win32":
            class SPS(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                            ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
            s = SPS()
            return bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)) and s.ACLineStatus == 0)      # 0 = not plugged in; 1 = plugged in; 255 = unknown
        base = Path("/sys/class/power_supply")
        if base.is_dir():
            mains = [p for p in base.iterdir() if (p / "type").exists() and (p / "type").read_text().strip() == "Mains"]
            if mains and any((p / "online").exists() for p in mains):
                return not any((p / "online").read_text().strip() == "1" for p in mains if (p / "online").exists())
    except Exception:
        pass
    return False


def should_wait() -> bool:
    """True when heavy background work should hold back right now."""
    return enabled() and on_battery()


def trim_memory() -> None:
    """Give back what the program no longer needs: collect garbage and (Windows) shrink the working set, so Task Manager shows what is
    really in use. The pages come back by themselves when they are needed again."""
    __import__("gc").collect()           # (not an import statement: a new import changes the packaged runtime, see tools/test_runtime_lock.py; gc is built into Python)
    try:
        if sys.platform == "win32":
            k = ctypes.windll.kernel32
            k.GetCurrentProcess.restype = ctypes.c_void_p
            k.SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
            k.SetProcessWorkingSetSize(k.GetCurrentProcess(), ctypes.c_size_t(-1).value, ctypes.c_size_t(-1).value)
    except Exception:
        pass
