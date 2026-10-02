"""Test: /api/storage/breakdown -- where the library's disk space goes, by folder/year/file type,
with trashed photos reported separately. Throw-away profile, no real images needed (bytes/paths are
synthetic; the endpoint only reads DB columns).

    py -3.12 tools/test_storage_breakdown.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8783
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_storage_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read() or b"{}")


srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    call("POST", "/api/settings/library", {"path": str(tmp / "lib")})

    os.environ["APPDATA"] = str(tmp / "appdata"); os.environ["LOCALAPPDATA"] = str(tmp / "local"); os.environ["USERPROFILE"] = str(tmp / "home")
    import sys as _s
    _s.path.insert(0, str(ROOT))
    from app import db as appdb
    con = appdb.connect()
    rows = [
        ("2024/a.jpg", 1_000_000, 1700000000, 0),
        ("2024/b.jpg", 2_000_000, 1700000001, 0),
        ("2023/c.mp4", 5_000_000, 1650000000, 0),
        ("2023/d.png", 500_000, None, 0),
        ("2022/e.jpg", 100, 1600000000, 1),   # trashed: excluded from the live totals
    ]
    for i, (rel, b, taken, trashed) in enumerate(rows):
        con.execute("INSERT INTO photos(sha256,rel_path,bytes,taken_at,trashed) VALUES(?,?,?,?,?)", (f"sha{i}", rel, b, taken, trashed))
    con.commit()

    d = call("GET", "/api/storage/breakdown")
    check("total excludes trashed photos", d["total_n"] == 4 and d["total_bytes"] == 8_500_000, (d["total_n"], d["total_bytes"]))
    check("trash is reported separately", d["trash_n"] == 1 and d["trash_bytes"] == 100, (d["trash_n"], d["trash_bytes"]))
    folders = {r["name"]: r["bytes"] for r in d["by_folder"]}
    check("folder totals add up", folders.get("2024") == 3_000_000 and folders.get("2023") == 5_500_000, folders)
    years = {r["name"]: r["n"] for r in d["by_year"]}
    check("a photo with no taken_at goes under '?'", years.get("?") == 1, years)
    types = {r["name"]: r["n"] for r in d["by_type"]}
    check("file types are grouped by extension, upper-cased", types.get("JPG") == 2 and types.get("MP4") == 1, types)
finally:
    srv.terminate()
    try:
        srv.wait(timeout=15)
    except Exception:
        srv.kill()

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
