"""New media made from existing ones (video frame, trimmed video, collage), drop / paste upload, merging two people, "New in library"
numbers, panorama and dominant-colour search -- and rule number one: the files they start from are never touched.
Throw-away profile, in-process (no server).

    py -3.12 tools/test_new_media.py
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_newmedia_test_"))
for d in ("appdata", "local", "home", "src"):
    (tmp / d).mkdir()
os.environ.update({"APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
                   "HOME": str(tmp / "home"), "PHOTAG_NO_OPEN": "1"})
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import db, ffmpeg, extras  # noqa: E402
from app.server import app  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def fp(folder: Path):
    return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in sorted(folder.iterdir()) if p.is_file()}


db.init_db()
cl = TestClient(app, base_url="http://127.0.0.1:8000")
src = tmp / "src"
Image.new("RGB", (600, 200), (10, 40, 220)).save(src / "wide_blue.jpg")            # a panorama (3:1), blue
Image.new("RGB", (300, 300), (220, 30, 30)).save(src / "red.jpg")
Image.new("RGB", (300, 200), (30, 200, 40)).save(src / "green.jpg")
vid = src / "clip.mp4"
if ffmpeg.available():
    subprocess.run([ffmpeg.exe(), "-y", "-f", "lavfi", "-i", "testsrc=duration=4:size=160x120:rate=10", "-pix_fmt", "yuv420p", str(vid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
before = fp(src)

con = db.connect()
ids = {}
for f in sorted(src.iterdir()):
    pid, _ = __import__("app.importer", fromlist=["x"])._ingest_file(con, f)
    ids[f.name] = pid
con.commit()
check("the test pictures and the video are in the catalog", len(ids) == (4 if vid.exists() else 3), ids)

# ---- search: panoramas and dominant colour
r = cl.post("/api/smart/ids", json={"criteria": {"pano": True}}).json()
check("panorama search finds only the wide picture", r == [ids["wide_blue.jpg"]] or r.get("ids") == [ids["wide_blue.jpg"]], r)
res_b = cl.post("/api/smart/ids", json={"criteria": {"dominant": ["blue"]}}).json()
res_r = cl.post("/api/smart/ids", json={"criteria": {"dominant": ["red"]}}).json()
ids_of = lambda x: x if isinstance(x, list) else x.get("ids")          # noqa: E731
check("colour search: blue finds the blue one, red the red one", ids_of(res_b) == [ids["wide_blue.jpg"]] and ids_of(res_r) == [ids["red.jpg"]], (res_b, res_r))
check("an unknown colour name matches nothing (never everything)", ids_of(cl.post("/api/smart/ids", json={"criteria": {"dominant": ["plaid"]}}).json()) == [])
check("the colour list counts the photos of each colour", {c["name"]: c["n"] for c in cl.get("/api/colors").json()}.get("green") == 1)
check("bucket names", extras.bucket(250, 20, 20) == "red" and extras.bucket(20, 20, 20) == "black" and extras.bucket(240, 240, 240) == "white" and extras.bucket(120, 60, 20) == "brown")

# ---- video frame and trim
if vid.exists():
    r = cl.post(f"/api/photo/{ids['clip.mp4']}/frame", json={"t": 1.5})
    j = r.json()
    check("a video frame is saved as a new photo", r.status_code == 200 and j["new"] and j["id"] != ids["clip.mp4"], j)
    ph = cl.get(f"/api/photo/{j['id']}").json()
    check("...it is a picture of the video's size, named after the video", not ph["is_video"] and ph["width"] == 160 and "clip frame" in ph["filename"], ph.get("filename"))
    check("a frame of a picture is refused", cl.post(f"/api/photo/{ids['red.jpg']}/frame", json={"t": 0}).status_code == 400)
    r = cl.post(f"/api/photo/{ids['clip.mp4']}/trim", json={"start": 1.0, "end": 3.0})
    j = r.json()
    check("a trimmed video is saved as a new video", r.status_code == 200 and j["new"] and cl.get(f"/api/photo/{j['id']}").json()["is_video"], j)
    r2 = cl.post(f"/api/photo/{ids['clip.mp4']}/trim", json={"start": 0.5, "end": 2.5, "exact": True})
    check("an exact (re-encoded) trim works too", r2.status_code == 200 and r2.json()["new"], r2.text[:100])
    check("an end before the start is refused", cl.post(f"/api/photo/{ids['clip.mp4']}/trim", json={"start": 3, "end": 1}).status_code == 400)

# ---- drop / paste
png = tmp / "p.png"
Image.new("RGB", (50, 40), (1, 2, 3)).save(png)
r = cl.post("/api/import-upload?name=image.png&modified=1500000000000", content=png.read_bytes())
j = r.json()
ph = cl.get(f"/api/photo/{j['id']}").json()
check("a pasted picture is imported and named 'Pasted ...'", r.status_code == 200 and j["new"] and ph["filename"].startswith("Pasted "), ph.get("filename"))
r = cl.post("/api/import-upload?name=holiday.png&modified=1500000000000", content=png.read_bytes())
check("the same picture again is not added twice", r.status_code == 200 and not r.json()["new"])
check("a dropped file that is not a picture or video is refused", cl.post("/api/import-upload?name=notes.txt", content=b"hello").status_code == 400)
check("a path in the name is dropped (nothing leaves the library)", cl.post("/api/import-upload?name=..%5C..%5Cevil.jpg", content=b"x").status_code in (200, 400)
      and not (tmp / "evil.jpg").exists())

# ---- merging people
a = con.execute("INSERT INTO people(name,source) VALUES('Dana','manual')").lastrowid
b = con.execute("INSERT INTO people(name,source) VALUES('Dana K','manual')").lastrowid
for pid, person, cluster in ((ids["red.jpg"], a, 7), (ids["green.jpg"], b, 8)):
    con.execute("INSERT INTO faces(photo_id,x1,y1,x2,y2,det_score,cluster_id,person_id) VALUES(?,0,0,1,1,0.9,?,?)", (pid, cluster, person))
con.execute("INSERT INTO faces(photo_id,x1,y1,x2,y2,det_score,cluster_id,person_id) VALUES(?,0,0,1,1,0.9,9,NULL)", (ids["wide_blue.jpg"],))
con.commit()
r = cl.post("/api/people/merge", json={"src": {"person": b}, "dst": {"person": a}})
n_a = con.execute("SELECT COUNT(*) FROM faces WHERE person_id=?", (a,)).fetchone()[0]
check("two people are merged: the faces move, the source person is gone", r.status_code == 200 and n_a == 2
      and not con.execute("SELECT 1 FROM people WHERE id=?", (b,)).fetchone())
r = cl.post("/api/people/merge", json={"src": {"cluster": 9}, "dst": {"person": a}})
check("an unnamed group is merged into a person", r.status_code == 200 and con.execute("SELECT COUNT(*) FROM faces WHERE person_id=?", (a,)).fetchone()[0] == 3)
check("a named person cannot be merged into an unnamed group, nor into itself",
      cl.post("/api/people/merge", json={"src": {"person": a}, "dst": {"cluster": 7}}).status_code == 400
      and cl.post("/api/people/merge", json={"src": {"person": a}, "dst": {"person": a}}).status_code == 400)

# ---- collage (a job, like the other merges)
r = cl.post("/api/merge", json={"ids": [ids["red.jpg"], ids["green.jpg"], ids["wide_blue.jpg"]], "kind": "collage"})
for _ in range(120):
    j = cl.get("/api/job/merge").json()
    if j["state"] in ("done", "error"):
        break
    time.sleep(0.5)
ph = cl.get(f"/api/photo/{j['result']['id']}").json() if j["state"] == "done" else {}
check("a collage of three photos becomes a new photo", r.status_code == 200 and j["state"] == "done" and "Collage" in ph.get("filename", ""), j)
check("a collage needs at least two photos", cl.post("/api/merge", json={"ids": [ids["red.jpg"]], "kind": "collage"}).status_code == 200
      and (time.sleep(1.5) or cl.get("/api/job/merge").json()["state"] == "error"))

# ---- New in library
s = cl.get("/api/library/new").json()
check("'New in library' counts what came in", s["total"] >= 7 and s["videos"] >= (1 if vid.exists() else 0) and s["no_place"] >= 1 and "unanalyzed" in s, s)

# ---- rule number one
check("rule number one: the files everything started from are untouched", fp(src) == before)
sys.exit(0 if all(res) else 1)
