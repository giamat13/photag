"""Test: a Google Takeout export that comes as several ZIP files (…-001.zip, …-002.zip, …).
Starts the server with a throw-away profile; builds three small ZIPs from the sample photos in tools/sandbox/photos.

    py -3.12 tools/test_takeout_parts.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHOTOS = ROOT / "tools" / "sandbox" / "photos"
PORT = 8776
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_takeout_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8"}
for d in ("appdata", "local", "home", "lib", "export"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=120) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def meta(desc=None, lat=None, lng=None, people=(), taken="1700000000"):
    m = {"title": "x", "photoTakenTime": {"timestamp": taken}, "creationTime": {"timestamp": taken}}
    if desc:
        m["description"] = desc
    if lat is not None:
        m["geoData"] = {"latitude": lat, "longitude": lng, "altitude": 0.0}
    if people:
        m["people"] = [{"name": n} for n in people]
    return json.dumps(m)


G = "Takeout/Google Photos/"
exp = tmp / "export"
stamp = "takeout-20260101T000000Z"
with zipfile.ZipFile(exp / f"{stamp}-001.zip", "w") as z:                  # photo + its sidecar + album info
    z.write(PHOTOS / "telaviv_1.jpg", G + "Trip/telaviv_1.jpg")
    z.writestr(G + "Trip/telaviv_1.jpg.json", meta("Beach", 32.0853, 34.7818, ["Dana"]))
    z.writestr(G + "Trip/metadata.json", json.dumps({"title": "Trip", "description": "Summer", "access": "private", "date": {"timestamp": "1700000000"}}))
(tmp / "hold").mkdir()
with zipfile.ZipFile(tmp / "hold" / f"{stamp}-002.zip", "w") as z:         # arrives later (part 002 is "still downloading")
    z.write(PHOTOS / "jerusalem.jpg", G + "Trip/jerusalem.jpg")             # its sidecar is in part 3!
    z.write(PHOTOS / "paris.jpg", G + "Other/paris.jpg")
    z.writestr(G + "Other/paris.jpg.json", meta("Eiffel", 48.8566, 2.3522))
with zipfile.ZipFile(exp / f"{stamp}-003.zip", "w") as z:
    z.writestr(G + "Trip/jerusalem.jpg.json", meta("Old city", 31.7683, 35.2137))
    z.writestr(G + "זיכרונות.json", json.dumps({"title": ["Summer 2024", "Winter 2023"]}))
with zipfile.ZipFile(exp / "takeout-20250101T000000Z-001.zip", "w") as z:   # another export in the same folder: must not be mixed in
    z.writestr(G + "Foreign/none.txt", "x")
(exp / "holiday.zip").write_bytes(b"not a takeout")

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

    def wait_import():
        while (j := call("GET", "/api/job/import")[1])["state"] not in ("done", "error", "idle"):
            time.sleep(0.2)
        return j

    # ---- finding the other parts
    code, info = call("GET", "/api/takeout/parts?path=" + urllib.parse.quote(str(exp / f"{stamp}-001.zip")))
    names = [p["name"] for p in info["parts"]]
    check("from part 001 the other parts of the SAME export are found", names == [f"{stamp}-001.zip", f"{stamp}-003.zip"], names)
    check("a gap in the numbering is reported (part 002 does not exist yet)", info["missing"] == [2], info["missing"])
    code, info = call("GET", "/api/takeout/parts?path=" + urllib.parse.quote(str(exp / f"{stamp}-003.zip")))
    check("from the LAST part the whole set is found too", [p["name"] for p in info["parts"]] == [f"{stamp}-001.zip", f"{stamp}-003.zip"])
    code, info = call("GET", "/api/takeout/parts?path=" + urllib.parse.quote(str(exp / "holiday.zip")))
    check("a ZIP that does not follow the Takeout naming is just itself", [p["name"] for p in info["parts"]] == ["holiday.zip"] and info["missing"] == [])
    code, _ = call("GET", "/api/takeout/parts?path=" + urllib.parse.quote(str(exp / "nope.zip")))
    check("a missing file -> 404", code == 404)
    shutil.move(str(tmp / "hold" / f"{stamp}-002.zip"), str(exp / f"{stamp}-002.zip"))      # part 002 finishes downloading

    # ---- the real thing: import all parts
    code, info = call("GET", "/api/takeout/parts?path=" + urllib.parse.quote(str(exp / f"{stamp}-001.zip")))
    parts = [p["path"] for p in info["parts"]]
    check("with part 002 present all three are found and nothing is missing", len(parts) == 3 and info["missing"] == [], [Path(p).name for p in parts])
    code, _ = call("POST", "/api/import", {"zip_paths": parts})
    j = wait_import()
    check("importing all parts together succeeds", code == 200 and j["state"] == "done", j.get("error"))
    ph = call("GET", "/api/photos?limit=99")[1]
    check("all 3 photos from the 3 parts are in the catalog", sorted(p["filename"] for p in ph) == ["jerusalem.jpg", "paris.jpg", "telaviv_1.jpg"], [p["filename"] for p in ph])
    byname = {p["filename"]: call("GET", f"/api/photo/{p['id']}")[1] for p in ph}
    check("a photo's sidecar that sits in ANOTHER part is applied (description, GPS)", byname["jerusalem.jpg"]["description"] == "Old city" and abs(byname["jerusalem.jpg"]["lat"] - 31.7683) < 1e-4)
    check("the sidecar in the same part is applied too (description, people tag)", byname["telaviv_1.jpg"]["description"] == "Beach" and any(x["name"] == "Dana" for x in byname["telaviv_1.jpg"]["people"]))
    albums = {a["name"]: a for a in call("GET", "/api/albums")[1]}
    check("an album that spans two parts is ONE album with both photos", "Trip" in albums and albums["Trip"].get("n", albums["Trip"].get("count")) in (2, None), albums.get("Trip"))
    trip = call("GET", f"/api/photos?album={albums['Trip']['id']}&limit=99")[1]
    check("album Trip holds telaviv + jerusalem (from different parts)", sorted(p["filename"] for p in trip) == ["jerusalem.jpg", "telaviv_1.jpg"])
    check("the album's own info (description) from its metadata.json is kept", albums["Trip"].get("description") == "Summer", albums["Trip"].get("description"))
    mem = call("GET", "/api/memories")[1]
    check("memory titles from a later part are imported", len(mem.get("titles", [])) == 2, mem.get("titles"))
    check("the other export in the same folder was not touched", "Foreign" not in albums)

    # ---- importing again (or only one part later) never duplicates
    code, _ = call("POST", "/api/import", {"zip_paths": parts[:1]})
    j = wait_import()
    check("importing a single part again is fine and adds nothing", j["state"] == "done" and len(call("GET", "/api/photos?limit=99")[1]) == 3)
    code, _ = call("POST", "/api/import", {"zip_path": parts[1]})
    j = wait_import()
    check("the old single-file request still works", code == 200 and j["state"] == "done")
    code, _ = call("POST", "/api/import", {"zip_paths": parts + [str(exp / "nope.zip")]})
    check("a list with a missing file is refused up front (nothing half-imported)", code == 404)
    code, _ = call("POST", "/api/import", {"zip_paths": []})
    check("an empty list is refused", code == 404)
    code, _ = call("POST", "/api/import", {"zip_paths": [str(exp / "holiday.zip")]})
    j = wait_import()
    check("a ZIP that is not a Takeout export fails with a clear error, not a crash", j["state"] == "error" and j.get("error"), j.get("error"))
finally:
    srv.terminate()
    try:
        srv.wait(timeout=15)
    except Exception:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
