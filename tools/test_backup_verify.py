"""Test: the weekly check of the newest backup (read-only). A good backup passes; a damaged ZIP, a missing photo set, a
missing file are reported; the check runs once a week, not more often; nothing is restored or changed.

    py -3.12 tools/test_backup_verify.py
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_verify_test_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))

from app import backup, config, db  # noqa: E402
from app.config import PATHS  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


try:
    config.set_library_root(tmp / "lib")
    PATHS.refresh()
    db.init_db().close()
    bk = tmp / "bk"
    backup.set_settings({"folder": str(bk), "include_media": True, "keep": 10})
    (PATHS.media / "2024").mkdir(parents=True, exist_ok=True)
    for i in range(6):
        (PATHS.media / "2024" / f"p{i}.jpg").write_bytes(os.urandom(20_000) + bytes([i]))

    check("no backup yet: nothing to check", backup.verify_last() is None and backup.verify_if_due() is None)
    m = backup.create_snapshot("manual")
    snap = bk / m["name"]

    r = backup.verify_last("test")
    check("a good backup passes", r and r["ok"] and not r["problems"], r)
    check("the outcome is remembered (the app shows it)", backup.health()["verify"]["ok"] is True and backup.health()["verify"]["name"] == m["name"])
    check("it is not checked again before a week has passed", backup.verify_due() is False and backup.verify_if_due() is None)
    os.environ["PHOTAG_VERIFY_EVERY_SECONDS"] = "0"
    check("...and is due again once the period is over", backup.verify_due() is True)

    # a missing photo file in the backup's set
    mdir = bk / m["media_dir"]
    victim = next(mdir.rglob("p3.jpg"))
    saved = victim.read_bytes()
    victim.unlink()
    r = backup.verify_last("test")
    check("a photo file missing from the backup is reported",
          r and not r["ok"] and r["problems"][0]["key"].startswith("The backup {name} should hold") and r["problems"][0]["vars"]["want"] == 6
          and r["problems"][0]["vars"]["found"] == 5, r)
    check("the problem is kept in the health info for the window", backup.health()["verify"]["ok"] is False)
    victim.write_bytes(saved)

    # the photo set is gone
    shutil.move(str(mdir), str(tmp / "moved"))
    r = backup.verify_last("test")
    check("a missing photo folder is reported", r and not r["ok"] and "missing from the backup folder" in r["problems"][0]["key"], r)
    shutil.move(str(tmp / "moved"), str(mdir))

    # a damaged ZIP (bytes in the middle overwritten)
    data = bytearray(snap.read_bytes())
    mid = len(data) // 2
    data[mid:mid + 64] = os.urandom(64)
    snap.write_bytes(bytes(data))
    r = backup.verify_last("test")
    check("a damaged backup file is reported", r and not r["ok"] and "damaged" in r["problems"][0]["key"], r)

    # a truncated file is damaged too
    snap.write_bytes(bytes(data[:100]))
    r = backup.verify_last("test")
    check("a truncated backup file is reported", r and not r["ok"], r)

    # the check never changes the live library or the backups
    check("the live catalog and photo files are untouched", len(list(PATHS.media.rglob("p*.jpg"))) == 6)

    # the job used by the "Check the backup" button
    from app import importer
    snap.unlink()
    shutil.rmtree(mdir)
    m2 = backup.create_snapshot("manual")
    prog = importer.Progress()
    backup.run_verify(prog)
    check("the button's job says there are no problems", prog.state == "done" and prog.key == "The backup check found no problems", (prog.state, prog.msg))
    (bk / m2["name"]).write_bytes(b"not a zip")
    prog = importer.Progress()
    backup.run_verify(prog)
    check("...and lists the problems when there are some", prog.state == "done" and prog.parts and "damaged" in prog.parts[0]["key"], (prog.state, prog.msg))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
