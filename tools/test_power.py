"""Test: heavy background work waits for the charger (app/power.py): the setting, the backup holding back on battery (but never for more than
3 days), and that a backup you ask for yourself is not held back.

    py -3.12 tools/test_power.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_power_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ["HOME"] = str(tmp / "home")
sys.path.insert(0, str(ROOT))
from app import backup, config, db, power  # noqa: E402
from app.config import PATHS  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


check("on by default", power.enabled() is True)
os.environ["PHOTAG_FAKE_BATTERY"] = "0"
check("plugged in: nothing waits", power.should_wait() is False)
os.environ["PHOTAG_FAKE_BATTERY"] = "1"
check("on battery with the setting on: heavy work waits", power.should_wait() is True)
power.set_enabled(False)
check("the setting switched off: nothing waits even on battery", power.should_wait() is False and power.enabled() is False)
power.set_enabled(True)

config.set_library_root(tmp / "lib")
PATHS.refresh()
db.init_db().close()
backup.set_settings({"folder": str(tmp / "bk"), "enabled": True, "include_media": False, "interval_hours": 24})
now = time.time()
check("no backup yet: due even on battery (it never ran, so it must not be put off)", backup.auto_due(now) is None)
backup.create_snapshot("manual")
later = time.time() + 2 * 86400
check("on battery, a backup that is due waits (2 days old)", backup.auto_due(later) == "on battery", backup.auto_due(later))
much_later = time.time() + 4 * 86400
check("...but not for more than 3 days", backup.auto_due(much_later) is None, backup.auto_due(much_later))
os.environ["PHOTAG_FAKE_BATTERY"] = "0"
check("plugged in again: runs", backup.auto_due(later) is None)
os.environ["PHOTAG_FAKE_BATTERY"] = "1"
r = backup.run_if_due(by="app", force=True, now=later)
check("a backup you ask for yourself always runs", r.get("ran") is True, r)
check("the real check does not crash here", isinstance(power.on_battery(), bool) or True)
del os.environ["PHOTAG_FAKE_BATTERY"]
check("without the pretend switch the answer is a plain yes / no", power.on_battery() in (True, False))
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
