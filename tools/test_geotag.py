"""Test: places for photos without GPS -- from a GPX track or from pictures taken at about the same time (suggested, applied on confirm).

    py -3.12 tools/test_geotag.py
"""
import calendar
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import geotag  # noqa: E402

PORT = 8794
APP = f"http://127.0.0.1:{PORT}"
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


T0 = calendar.timegm((2024, 5, 1, 10, 0, 0))
GPX = """<?xml version="1.0"?><gpx><trk><trkseg>
<trkpt lat="32.0" lon="34.0"><time>2024-05-01T10:00:00Z</time></trkpt>
<trkpt lat="32.1" lon="34.2"><time>2024-05-01T10:10:00.5Z</time></trkpt>
<trkpt lat="40.0" lon="30.0"><time>2024-05-01T15:00:00+02:00</time></trkpt>
<trkpt lat="999" lon="30.0"><time>2024-05-01T16:00:00Z</time></trkpt>
<trkpt lat="1" lon="1"></trkpt></trkseg></trk></gpx>"""

# ---- the library functions
tr = geotag.parse_gpx(GPX)
check("the track: 3 good points, bad ones (no time, impossible place) ignored, with the zone of +02:00 understood", len(tr) == 3 and abs(tr[2][0] - (T0 + 3 * 3600)) < 1, tr)
r = {x["id"]: x for x in geotag.from_track([(1, T0 + 300), (2, T0 + 3 * 3600 + 120), (3, T0 - 1200), (4, T0 + 5 * 3600)], tr, offset_min=0, max_gap_s=900)}
check("between two points: on the line between them", abs(r[1]["lat"] - 32.05) < 0.001 and abs(r[1]["lng"] - 34.1) < 0.001, r[1])
check("near the last point: that point", r[2]["lat"] == 40.0)
check("a picture further than the allowed gap from the track gets nothing", 3 not in r and 4 not in r)
off = {x["id"]: x for x in geotag.from_track([(1, T0 + 2 * 3600 + 300)], tr, offset_min=120, max_gap_s=900)}
check("the camera's zone (+120 minutes) is taken into account", 1 in off and abs(off[1]["lat"] - 32.05) < 0.001, off)
nb = {x["id"]: x for x in geotag.from_neighbors([(1, T0 + 100), (2, T0 + 90000)], [(10, T0, 1.5, 2.5), (11, T0 + 3000, 3.0, 4.0)], max_gap_s=1800)}
check("neighbours: the nearest picture in time gives its place", nb[1]["lat"] == 1.5 and nb[1]["from_id"] == 10 and 2 not in nb, nb)

# ---- the server
tmp = Path(tempfile.mkdtemp(prefix="photag_geotag_"))
for d in ("a", "l", "h"):
    (tmp / d).mkdir()
env = {**os.environ, "APPDATA": str(tmp / "a"), "LOCALAPPDATA": str(tmp / "l"), "USERPROFILE": str(tmp / "h"), "HOME": str(tmp / "h"), "PYTHONIOENCODING": "utf-8"}


def call(m, p, b=None):
    rq = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(rq, timeout=60)
        return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    lib = Path(call("GET", "/api/status")[1]["library_root"])
    con = sqlite3.connect(lib / "catalog.db")
    rows = [(1, "with.jpg", T0, 31.0, 35.0), (2, "near.jpg", T0 + 600, None, None), (3, "far.jpg", T0 + 86400, None, None), (4, "notime.jpg", None, None, None)]
    for i, n, t, la, lo in rows:
        con.execute("INSERT INTO photos(id, sha256, filename, rel_path, taken_at, lat, lng, trashed) VALUES(?,?,?,?,?,?,?,0)", (i, f"s{i}", n, n, t, la, lo))
    con.commit(); con.close()
    code, r = call("POST", "/api/geotag/suggest", {"mode": "photos", "max_gap_min": 30})
    check("suggest from pictures: only the near one, with the place of the picture before it", code == 200 and [i["id"] for i in r["items"]] == [2] and r["items"][0]["lat"] == 31.0 and r["items"][0]["source"] == "photo", r)
    check("it says how many pictures have no place (only those with a time)", r["without_place"] == 2, r)
    code, r = call("POST", "/api/geotag/suggest", {"mode": "gpx", "gpx": GPX, "offset_min": 0, "max_gap_min": 30})
    check("suggest from a GPX file", code == 200 and [i["id"] for i in r["items"]] == [2] and abs(r["items"][0]["lat"] - 32.1) < 0.01 and r["items"][0]["source"] == "gpx", r)
    check("a GPX file without track points is refused (400)", call("POST", "/api/geotag/suggest", {"mode": "gpx", "gpx": "<gpx></gpx>"})[0] == 400)
    check("nothing was changed by suggesting", sqlite3.connect(lib / "catalog.db").execute("SELECT COUNT(*) FROM photos WHERE lat IS NULL").fetchone()[0] == 3)
    code, r = call("POST", "/api/geotag/apply", {"items": [{"id": 2, "lat": 32.1, "lng": 34.2}, {"id": 1, "lat": 5, "lng": 5}, {"id": 3, "lat": 999, "lng": 1}, {"id": "x"}]})
    c2 = sqlite3.connect(lib / "catalog.db")
    check("apply: sets the place of the confirmed one only", code == 200 and r["applied"] == 1 and c2.execute("SELECT lat, lng FROM photos WHERE id=2").fetchone() == (32.1, 34.2), r)
    check("apply: a place that is already there is never moved, an impossible one is refused", c2.execute("SELECT lat FROM photos WHERE id=1").fetchone()[0] == 31.0 and c2.execute("SELECT lat FROM photos WHERE id=3").fetchone()[0] is None)
    c2.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
