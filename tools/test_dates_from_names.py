"""Dates from file names: only photos with NO date get one from their name; a date that is already there is never replaced.
Throw-away profile, no server needed.

    py -3.12 tools/test_dates_from_names.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_dates_test_"))
for d in ("appdata", "local", "home"):
    (tmp / d).mkdir()
os.environ.update({"APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
                   "HOME": str(tmp / "home"), "PHOTAG_NO_OPEN": "1"})
sys.path.insert(0, str(ROOT))

import _client  # noqa: E402  (tools/_client.py)

from app import db  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


db.init_db()
con = db.connect()
cols = [r["name"] for r in con.execute("PRAGMA table_info(photos)")]


def add(sha, name, taken):
    con.execute("INSERT INTO photos(sha256,filename,rel_path,mime,is_video,width,height,bytes,taken_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (sha, name, name, "image/jpeg", 0, 10, 10, 1, taken))
    return con.execute("SELECT id FROM photos WHERE sha256=?", (sha,)).fetchone()["id"]


a = add("a", "IMG-20230514-WA0001.jpg", None)          # no date, date in the name -> gets it
b = add("b", "IMG-20200101-WA0002.jpg", 1700000000)    # has a different date -> untouched
c = add("c", "holiday.jpg", None)                      # no date, nothing in the name -> untouched
d = add("d", "Screenshot_20240101-101530.png", 0)      # date 0 counts as no date
con.commit()

srv, cl = _client.start_server(8806, {**os.environ, "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999"})
r = cl.post("/api/dates/from-names", json={"apply": False}).json()
check("preview counts only the photos without a date whose name has one", r["count"] == 2 and not r["applied"], r)


def taken(i):
    return db.connect().execute("SELECT taken_at FROM photos WHERE id=?", (i,)).fetchone()[0]


check("a preview changes nothing", taken(a) in (None, 0) and taken(d) in (None, 0))
r = cl.post("/api/dates/from-names", json={"apply": True}).json()
check("apply reports it applied", r["applied"] and r["count"] == 2, r)
check("the photo without a date got the date from its name", taken(a) and taken(a) > 1600000000 and taken(d) and taken(d) > 1700000000, (taken(a), taken(d)))
check("a photo that already had a date (even a different one) is untouched", taken(b) == 1700000000, taken(b))
check("a photo with no date in its name is untouched", taken(c) in (None, 0), taken(c))
check("after applying there is nothing left to fix", cl.post("/api/dates/from-names", json={"apply": False}).json()["count"] == 0)
srv.terminate()
sys.exit(0 if all(res) else 1)
