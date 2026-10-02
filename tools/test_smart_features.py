"""Test: quality score, duplicate / similar groups (by picture, time and place), library cleanup report, ranking a selection,
smart collections (rules evaluated by the server), search-by-meaning plumbing, and the closed-eyes geometry.
The CLIP model itself is not downloaded here: its tokenizer and ranking are tested with small stand-ins. Throw-away profile.

    py -3.12 tools/test_smart_features.py
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "tools" / "sandbox" / "photos"
PORT = 8783
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_smart_test_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ.update(PYTHONIOENCODING="utf-8", PHOTAG_NO_OPEN="1", PHOTAG_BACKUP_START_DELAY="9999")
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


# ---- pure functions -----------------------------------------------------------------------------------------------
from app import analysis, semantic, smart  # noqa: E402

img = Image.open(SAMPLES / "paris.jpg").convert("RGB")
m_sharp = analysis.measure(img, "a.jpg", "JPEG", True)
m_blur = analysis.measure(img.filter(ImageFilter.GaussianBlur(6)), "b.jpg", "JPEG", True)
m_dark = analysis.measure(ImageEnhance.Brightness(img).enhance(0.08), "c.jpg", "JPEG", True)
check("a sharp photo scores clearly higher than the same photo blurred", m_sharp["score"] >= m_blur["score"] + 25, (m_sharp["score"], m_blur["score"]))
check("a very dark photo scores low", m_dark["score"] < m_sharp["score"] - 40 and m_dark["mean"] < analysis.DARK_MEAN, (m_dark["score"], m_dark["mean"]))
check("scores stay inside 1..100", all(1 <= x["score"] <= 100 for x in (m_sharp, m_blur, m_dark)))
check("closed eyes lower the score", analysis.quality_score(m_sharp, 1) < analysis.quality_score(m_sharp, 0), (analysis.quality_score(m_sharp, 1), analysis.quality_score(m_sharp, 0)))
check("a blurred photo is under the blur threshold, a sharp one is not", m_blur["sharp"] < analysis.BLUR_SHARP < m_sharp["sharp"], (m_blur["sharp"], m_sharp["sharp"]))
font = ImageFont.load_default(size=22)
page = Image.new("RGB", (900, 2000), (245, 243, 238))
d = ImageDraw.Draw(page)
for y in range(60, 1900, 48):
    d.text((60, y), "MILK 3.5% 1L      12.90  x2", fill=(30, 30, 30), font=font)
check("a page of printed lines is flagged as a receipt / document", analysis.measure(page, "IMG_1.jpg", "JPEG", True)["is_receipt"] == 1)
check("a photo is not flagged as a receipt", m_sharp["is_receipt"] == 0)
shot = Image.new("RGB", (1080, 2400), (240, 240, 250))
check("a screenshot is recognised by its name", analysis.measure(shot, "Screenshot_2024-01-01.png", "PNG", False)["is_screenshot"] == 1)
check("... and by its PNG screen size without camera data", analysis.measure(shot, "x.png", "PNG", False)["is_screenshot"] == 1)
check("... but not a photo with camera data", analysis.measure(shot, "x.jpg", "JPEG", True)["is_screenshot"] == 0)
check("a flat image has no hash (it would match every other flat image)", analysis.dhash(Image.new("RGB", (64, 64), (10, 10, 10))) is None)
check("the same picture, resized and re-saved, has a nearly identical hash",
      analysis.hamming(analysis.dhash(img), analysis.dhash(img.resize((450, 300)))) <= analysis.DUP_DIST)
check("different pictures are far apart", analysis.hamming(analysis.dhash(img), analysis.dhash(Image.open(SAMPLES / "telaviv_2.jpg").convert("RGB"))) > analysis.SIM_DIST)


def ell(cx, cy, a, b, rot=0.5, n=8):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    x, y = a * np.cos(t), b * np.sin(t)
    c, s = math.cos(rot), math.sin(rot)
    return np.stack([cx + x * c - y * s, cy + x * s + y * c], 1)


lm = np.zeros((106, 2))
lm[analysis.LEFT_EYE], lm[analysis.RIGHT_EYE] = ell(100, 100, 20, 8), ell(200, 100, 20, 8)
check("open eyes (tilted head) are not reported closed", not analysis.face_eyes_closed(lm))
lm[analysis.LEFT_EYE], lm[analysis.RIGHT_EYE] = ell(100, 100, 20, 2), ell(200, 100, 20, 2)
check("closed eyes are reported closed", analysis.face_eyes_closed(lm))

# tokenizer stand-in: tiny vocab and merges give the same ids the algorithm must
tok = semantic.Tokenizer({"a</w>": 1, "b</w>": 2, "ab</w>": 3, "a": 4, "b": 5, "c</w>": 6}, ["a b</w>"])
check("the CLIP tokenizer merges pairs and wraps the ids in BOS/EOS", tok.encode("ab c") == [semantic.BOS, 3, 6, semantic.EOS], tok.encode("ab c"))
rng = np.random.default_rng(1)
E = rng.normal(size=(200, 512)).astype(np.float32)
E /= np.linalg.norm(E, axis=1, keepdims=True)
q = E[17] * 0.9 + rng.normal(size=512).astype(np.float32) * 0.01
q /= np.linalg.norm(q)
found = semantic.rank_embeddings(np.arange(200) + 1000, E, q)
check("search ranks the matching embedding first", list(found)[0] == 1017, list(found)[:3])
check("English / other-language queries are told apart", semantic.is_english("dog in the snow") and not semantic.is_english("חוף בשקיעה"))

# ---- groups on a fabricated catalog ---------------------------------------------------------------------------------
from app import config, db  # noqa: E402
from app.config import PATHS  # noqa: E402

config.set_library_root(tmp / "lib0")
PATHS.refresh()
con = db.init_db()


def add(pid, taken, lat, lng, phash, score, w=4000, h=3000, size=3_000_000, eyes=None):
    con.execute("INSERT INTO photos(id,sha256,filename,rel_path,mime,width,height,bytes,taken_at,lat,lng) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (pid, f"sha{pid}", f"IMG_{pid}.jpg", f"2024/IMG_{pid}.jpg", "image/jpeg", w, h, size, taken, lat, lng))
    con.execute("INSERT INTO photo_analysis(photo_id,sha,version,phash,sharp,mean,p5,p95,clip_dark,clip_white,sat,ink,white,score,eyes_closed) "
                "VALUES(?,?,?,?,100,120,20,220,0,0,.3,0,.2,?,?)", (pid, f"sha{pid}", analysis.ANALYSIS_VERSION, phash, score, eyes))


def sgn(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >= 1 << 63 else v


base = 0x0F0F_3C3C_A5A5_F00F
T = 1_700_000_000
add(1, T, 32.08, 34.78, sgn(base), 70)                       # a burst: 1, 2, 3 within seconds, a few bits apart
add(2, T + 2, 32.08, 34.78, sgn(base ^ 0b1010_0000_0000_0000_0010), 91)       # best
add(3, T + 4, 32.08, 34.78, sgn(base ^ 0b0100_0000_0001_0000_0000_0000_1), 60)
add(4, T + 99999, None, None, sgn(base), 80, size=1_000_000)                  # a copy of 1, taken at another time
other = 0x5A5A_00FF_1234_C3C3
add(5, T + 5, 32.08, 34.78, sgn(other), 50)                  # unrelated picture, same moment
add(6, T + 9_000_000, 48.85, 2.35, sgn(base ^ ((1 << 11) - 1)), 99)          # 11 bits away, other time and place -> not grouped
add(7, T + 7, 32.08, 34.78, sgn(0x7777_8888_9999_AAAA), 90, eyes=1)          # a different picture
add(8, T + 8, 32.0801, 34.7801, sgn(0x7777_8888_9999_AAAA ^ 0xF), 90, eyes=0)  # 4 bits from 7, 8 seconds later
con.commit()
groups = analysis.find_groups(con)
sets = {frozenset(p["id"] for p in g["photos"]): g for g in groups}
check("the burst and the copy form one group", frozenset({1, 2, 3, 4}) in sets, [sorted(s) for s in sets])
g = sets.get(frozenset({1, 2, 3, 4}), {})
check("the best shot of the group is the highest score", g.get("best") == 2, g.get("best"))
check("the group says it is about time and place", {"time", "place"} <= set(g.get("why", [])), g.get("why"))
check("a different picture at the same moment is not pulled in", all(5 not in s for s in sets))
check("a look-alike at another time and place is not pulled in", all(6 not in s for s in sets))
g78 = sets.get(frozenset({7, 8}), {})
check("a close pair is found and the open-eyed one wins a tie", g78.get("best") == 8, g78.get("best"))
con.close()

# ---- through the server --------------------------------------------------------------------------------------------
src = tmp / "src"
src.mkdir()
for n in ("paris", "jerusalem", "telaviv_1", "telaviv_2"):
    Image.open(SAMPLES / f"{n}.jpg").convert("RGB").save(src / f"{n}.jpg", quality=92)
Image.open(SAMPLES / "paris.jpg").convert("RGB").resize((450, 300)).save(src / "paris_small.jpg", quality=80)          # same picture, other file
Image.open(SAMPLES / "jerusalem.jpg").convert("RGB").filter(ImageFilter.GaussianBlur(7)).save(src / "blurry.jpg", quality=92)
ImageEnhance.Brightness(Image.open(SAMPLES / "telaviv_1.jpg").convert("RGB")).enhance(0.07).save(src / "night.jpg", quality=92)
Image.new("RGB", (1080, 2400), (240, 240, 250)).save(src / "Screenshot_20240101.png")
page.save(src / "receipt.jpg", quality=92)


def call(m, p, b=None, expect=200):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=120).read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == expect:
            return {"status": e.code, **json.loads(e.read() or b"{}")}
        raise


def job(name, secs=120):
    t0 = time.time()
    while time.time() - t0 < secs:
        p = call("GET", f"/api/job/{name}")
        if p["state"] in ("done", "error"):
            return p
        time.sleep(0.3)
    return {"state": "timeout"}


srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env={**os.environ},
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    call("POST", "/api/settings/library", {"path": str(tmp / "lib")})
    call("POST", "/api/import-folder", {"paths": [str(p) for p in sorted(src.iterdir())]})
    p = job("import")
    check("sample photos imported", p["state"] == "done" and len(call("GET", "/api/photos?limit=999")) == 9, p.get("msg"))
    st = call("GET", "/api/analysis/status")
    check("nothing is analysed yet", st["pending"] == 9, st)
    call("POST", "/api/analysis/run", {"eyes": False})
    p = job("analysis")
    check("the analysis job finishes", p["state"] == "done", p)
    st = call("GET", "/api/analysis/status")
    check("everything is analysed", st["pending"] == 0, st)
    photos = {x["filename"]: x for x in call("GET", "/api/photos?limit=999")}
    check("each photo carries its score", all(x["score"] and 1 <= x["score"] <= 100 for x in photos.values()), {k: v["score"] for k, v in photos.items()})
    check("the sharp original outscores its blurred twin", photos["jerusalem.jpg"]["score"] > photos["blurry.jpg"]["score"] + 20)
    gr = call("GET", "/api/analysis/groups")["groups"]
    fn = {g["best"]: g for g in gr}
    paris = [g for g in gr if {photos["paris.jpg"]["id"], photos["paris_small.jpg"]["id"]} <= {x["id"] for x in g["photos"]}]
    check("a resized copy is found as a duplicate of its original", len(paris) == 1 and paris[0]["kind"] == "copy", gr)
    check("...and the bigger original is the one recommended", paris and paris[0]["best"] == photos["paris.jpg"]["id"], paris and paris[0]["best"])
    rep = call("GET", "/api/analysis/cleanup")["categories"]
    ids = lambda k: set(rep[k]["ids"])
    check("cleanup: the screenshot", ids("screenshot") == {photos["Screenshot_20240101.png"]["id"]}, rep["screenshot"])
    check("cleanup: the receipt", ids("receipt") == {photos["receipt.jpg"]["id"]}, rep["receipt"])
    check("cleanup: the dark photo", ids("dark") == {photos["night.jpg"]["id"]}, rep["dark"])
    check("cleanup: the blurry photo, and only that", ids("blurry") == {photos["blurry.jpg"]["id"]}, rep["blurry"])
    rk = call("POST", "/api/analysis/rank", {"ids": [photos["blurry.jpg"]["id"], photos["jerusalem.jpg"]["id"], photos["night.jpg"]["id"]]})["ranked"]
    check("ranking puts the sharp photo first and numbers the ranks", rk[0]["id"] == photos["jerusalem.jpg"]["id"] and [x["rank"] for x in rk] == [1, 2, 3], rk)
    check("ranking one photo is refused", call("POST", "/api/analysis/rank", {"ids": [1]}, expect=400)["status"] == 400)
    # a changed file is analysed again, an unchanged one is not
    sem = call("GET", "/api/semantic/status")
    check("search by meaning reports that the model is missing", sem["ready"] is False and sem["indexed"] == 0 and sem["total"] == 9, sem)
    r = call("GET", "/api/semantic/search?q=beach", expect=409)
    check("searching without the model is refused politely", r["status"] == 409)
    call("POST", "/api/semantic/index", {"download": False})
    p = job("semantic")
    check("indexing without consent to download does not download", p["state"] == "error", p)

    # smart collections
    pid = photos["paris.jpg"]["id"]
    call("PATCH", f"/api/photo/{pid}", {"rating": 5, "add_tags": ["Trip"]})
    call("PATCH", f"/api/photo/{photos['jerusalem.jpg']['id']}", {"rating": 3})
    call("POST", "/api/searches", {"name": "five-star trip", "criteria": {"smart": 1, "minRating": 4, "tags": ["trip"]}})
    call("POST", "/api/searches", {"name": "empty rules", "criteria": {"smart": 1}})
    sc = {x["name"]: x for x in call("GET", "/api/searches")}
    check("a smart collection finds the photos that satisfy every rule", sc["five-star trip"]["ids"] == [pid] and sc["five-star trip"]["smart"], sc["five-star trip"])
    check("a collection without rules matches nothing (never everything)", sc["empty rules"]["ids"] == [])
    call("PATCH", f"/api/photo/{photos['telaviv_1.jpg']['id']}", {"rating": 4, "add_tags": ["trip"]})
    sc = {x["name"]: x for x in call("GET", "/api/searches")}
    check("it fills itself when another photo qualifies", set(sc["five-star trip"]["ids"]) == {pid, photos["telaviv_1.jpg"]["id"]})
    r = call("POST", "/api/smart/ids", {"criteria": {"years": [1990], "minScore": 1}})
    check("year and score rules combine", r["ids"] == [], r)
    r = call("POST", "/api/smart/ids", {"criteria": {"minScore": 60, "kind": "photo"}})
    check("a minimum quality score rule works", set(r["ids"]) >= {photos["jerusalem.jpg"]["id"]} and photos["blurry.jpg"]["id"] not in r["ids"], r)
    config.set_library_root(tmp / "lib")                  # this process writes into the server's library
    PATHS.refresh()
    con = db.connect()
    con.execute("INSERT INTO people(id,name,source) VALUES(1,'Dana','takeout'),(2,'Ron','takeout')")
    for i, x in enumerate(photos.values()):
        if i < 6:
            con.execute("INSERT INTO photo_people(photo_id,person_id,source) VALUES(?,1,'takeout')", (x["id"],))
        if i < 2:
            con.execute("INSERT INTO photo_people(photo_id,person_id,source) VALUES(?,2,'takeout')", (x["id"],))
    con.commit()
    anyp = call("POST", "/api/smart/ids", {"criteria": {"people": [1, 2]}})["ids"]
    allp = call("POST", "/api/smart/ids", {"criteria": {"people": [1, 2], "peopleAll": 1}})["ids"]
    check("people: any of them / all of them", len(anyp) == 6 and len(allp) == 2, (len(anyp), len(allp)))
    g = call("POST", "/api/smart/from-google", {"min_photos": 5})
    check("collections from Google's people tags (only people with enough photos)", g == {"created": 1, "people": 1}, g)
    check("...and they are not created twice", call("POST", "/api/smart/from-google", {"min_photos": 5})["created"] == 0)
    sc = {x["name"]: x for x in call("GET", "/api/searches")}
    check("the Google people collection holds that person's photos", len(sc["Dana"]["ids"]) == 6, sc["Dana"]["ids"])
    old = call("POST", "/api/searches", {"name": "old style", "criteria": {"kind": "photo", "minMB": 0.000001}})
    sc = {x["name"]: x for x in call("GET", "/api/searches")}
    check("an Advanced Search saved earlier still works and is not a smart collection", not sc["old style"]["smart"] and len(sc["old style"]["ids"]) == 9, len(sc["old style"]["ids"]))
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
