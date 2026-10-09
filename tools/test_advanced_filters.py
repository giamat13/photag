"""Test: everything the Library Filter can do is also in Advanced Search (text, flag, rating, color label, edited, month,
orientation) plus more (keywords, with / without keywords, location and faces, favorites, people, albums, weekday, time of
day, megapixels, when it was added, quality), evaluated by the server for saved and unsaved searches alike. Each answer is
compared with what the photo list itself says. Throw-away profile.

    py -3.12 tools/test_advanced_filters.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8785
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_advfilters_test_"))
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


def ids(c):
    return set(call("POST", "/api/smart/ids", {"criteria": c})["ids"])


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
    files = [str(p) for p in sorted((ROOT / "tools" / "sandbox" / "photos").iterdir()) if p.suffix.lower() == ".jpg"]
    call("POST", "/api/import-folder", {"paths": files, "keywords": [], "album": None})
    while call("GET", "/api/job/import")["state"] not in ("done", "error"):
        time.sleep(0.3)
    photos = sorted(call("GET", "/api/photos?limit=999"), key=lambda p: p["id"])
    P = [p["id"] for p in photos]
    check("six photos imported", len(P) == 6, len(P))

    # give them different marks
    call("PATCH", "/api/photos", {"ids": [P[0]], "rating": 5, "flag": 1, "label": "red", "favorited": 1, "add_tags": ["beach", "sun"]})
    call("PATCH", "/api/photos", {"ids": [P[1]], "rating": 3, "flag": -1, "label": "blue", "add_tags": ["beach"]})
    call("PATCH", "/api/photos", {"ids": [P[2]], "rating": 1, "description": "sunset memory"})
    call("POST", "/api/albums", {"name": "Trip", "ids": [P[3], P[4]]})
    album = next(a["id"] for a in call("GET", "/api/albums") if a["name"] == "Trip")
    photos = sorted(call("GET", "/api/photos?limit=999"), key=lambda p: p["id"])
    byid = {p["id"]: p for p in photos}
    name = {p["id"]: p["filename"] for p in photos}

    def same(label, crit, want):
        got = ids(crit)
        check(label, got == set(want), (sorted(name[i] for i in got), sorted(name[i] for i in want)))

    same("text, any field: a keyword", {"q": "beach"}, [P[0], P[1]])
    same("text, any field: the description", {"q": "sunset"}, [P[2]])
    same("text, file name only: a keyword does not count", {"q": "beach", "qf": "name"}, [])
    stem = name[P[3]].split(".")[0]
    same("text, file name only", {"q": stem, "qf": "name"}, [i for i in P if stem in name[i]])
    same("flag: picks", {"flags": ["pick"]}, [P[0]])
    same("flag: rejects", {"flags": ["rej"]}, [P[1]])
    same("flag: unflagged", {"flags": ["none"]}, P[2:])
    same("flag: picks or rejects", {"flags": ["pick", "rej"]}, P[:2])
    same("rating at least 3", {"rating": 3, "ratingOp": ">="}, P[:2])
    same("rating at most 3", {"rating": 3, "ratingOp": "<="}, [i for i in P if (byid[i]["rating"] or 0) <= 3])
    same("rating exactly 3", {"rating": 3, "ratingOp": "="}, [P[1]])
    same("color: red", {"colors": ["red"]}, [P[0]])
    same("color: red or blue", {"colors": ["red", "blue"]}, P[:2])
    same("color: none", {"colors": ["none"]}, P[2:])
    same("favorites", {"fav": True}, [P[0]])
    same("edited (none was edited)", {"edited": True}, [i for i in P if byid[i]["edited"]])
    same("keywords: any of", {"keywords": "sun, beach"}, P[:2])
    same("keywords: all of", {"keywords": "sun, beach", "keywordsAll": True}, [P[0]])
    same("with keywords", {"kw": "has"}, P[:2])
    same("without keywords", {"kw": "none"}, P[2:])
    same("with location", {"gps": "has"}, [i for i in P if byid[i]["lat"] is not None])
    same("without location", {"gps": "none"}, [i for i in P if byid[i]["lat"] is None])
    same("without faces (none were detected)", {"faces": "none"}, P)
    same("with faces", {"faces": "has"}, [])
    same("album", {"albumIds": [album]}, [P[3], P[4]])

    def months(ms):
        return [i for i in P if byid[i]["taken_at"] and "%02d" % time.localtime(byid[i]["taken_at"]).tm_mon in ms]
    m0 = "%02d" % time.localtime(byid[P[0]]["taken_at"]).tm_mon
    same("month", {"months": [m0]}, months([m0]))

    def wd(i):                                     # 0 = Sunday, like strftime('%w') and JavaScript
        return (time.localtime(byid[i]["taken_at"]).tm_wday + 1) % 7
    d0 = wd(P[0])
    same("day of week", {"weekdays": [d0]}, [i for i in P if byid[i]["taken_at"] and wd(i) == d0])
    h0 = time.localtime(byid[P[0]]["taken_at"]).tm_hour
    same("time of day (one hour)", {"hourFrom": h0, "hourTo": h0}, [i for i in P if byid[i]["taken_at"] and time.localtime(byid[i]["taken_at"]).tm_hour == h0])
    nxt = (h0 + 1) % 24
    over = [i for i in P if byid[i]["taken_at"] and time.localtime(byid[i]["taken_at"]).tm_hour in (h0, nxt)]
    same("time of day over midnight is handled" if h0 == 23 else "time of day (two hours)", {"hourFrom": h0, "hourTo": nxt} if h0 != 23 else {"hourFrom": 23, "hourTo": 0}, over if h0 != 23 else [i for i in P if byid[i]["taken_at"] and time.localtime(byid[i]["taken_at"]).tm_hour in (23, 0)])

    def orient(p):
        w, h = p["width"] or 0, p["height"] or 0
        return "unknown" if not w or not h else "landscape" if w > h * 1.05 else "portrait" if h > w * 1.05 else "square"
    for o in ("landscape", "portrait", "square"):
        same(f"orientation: {o}", {"orients": [o]}, [i for i in P if orient(byid[i]) == o])
    mp = sorted((byid[i]["width"] or 0) * (byid[i]["height"] or 0) / 1e6 for i in P)
    cut = (mp[2] + mp[3]) / 2
    same("megapixels at least", {"minMP": cut}, [i for i in P if (byid[i]["width"] or 0) * (byid[i]["height"] or 0) / 1e6 >= cut])
    same("megapixels at most", {"maxMP": cut}, [i for i in P if (byid[i]["width"] or 0) * (byid[i]["height"] or 0) / 1e6 <= cut])
    today = time.strftime("%Y-%m-%d")
    same("added to the library from today", {"addedFrom": today}, P)
    same("added to the library before today", {"addedTo": time.strftime("%Y-%m-%d", time.localtime(time.time() - 3 * 86400))}, [])
    same("everything at once narrows down", {"flags": ["pick"], "rating": 4, "ratingOp": ">=", "colors": ["red"], "fav": True, "keywords": "beach", "kw": "has"}, [P[0]])
    same("an empty rule set matches nothing", {}, [])

    # places: a box and a circle
    pos = [byid[i] for i in P if byid[i]["lat"] is not None]
    p0 = pos[0]
    box = [p0["lat"] - 0.05, p0["lat"] + 0.05, p0["lng"] - 0.05, p0["lng"] + 0.05]
    same("a place as a box", {"place": {"lat": p0["lat"], "lng": p0["lng"], "km": 0, "box": box}},
         [p["id"] for p in pos if box[0] <= p["lat"] <= box[1] and box[2] <= p["lng"] <= box[3]])
    same("a place as a circle", {"place": {"lat": p0["lat"], "lng": p0["lng"], "km": 1}}, [p0["id"]] + [p["id"] for p in pos if p is not p0 and p["lat"] == p0["lat"] and p["lng"] == p0["lng"]])

    # saved: the new keys do not turn a search into a smart collection
    sid = call("POST", "/api/searches", {"name": "Picks", "criteria": {"flags": ["pick"], "gps": "has", "months": [m0]}})["id"]
    saved = next(s for s in call("GET", "/api/searches") if s["id"] == sid)
    check("a saved search with the new keys is a search, not a smart collection", saved["smart"] is False and set(saved["ids"]) == ids({"flags": ["pick"], "gps": "has", "months": [m0]}), saved["smart"])

    # ---- the form
    with sync_playwright() as pw:
        b = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)

        last = {}

        def search(setup):
            pg.evaluate("SEARCH_TMP = null; setSource(srcFromKey('all'))")
            pg.wait_for_timeout(400)
            pg.evaluate("advancedSearch()")
            pg.wait_for_selector("#as-text")
            setup()
            pg.click("#as-go")
            try:
                pg.wait_for_function("S.src.kind === 'search' && S.base.length >= 0 && !document.querySelector('.modal-box')", timeout=8000)
            except Exception:
                pass
            pg.wait_for_timeout(500)
            last["info"] = pg.evaluate("JSON.stringify([S.src.kind, S.src.crit && Object.keys(S.src.crit).filter(k => { const v = S.src.crit[k]; return v && !(Array.isArray(v) && !v.length) && v !== 'any' && v !== '>=' && v !== 'all'; })])")
            return pg.evaluate("S.list.length")

        controls = ["#as-text", "#as-qf", "#as-flags", "#as-rating", "#as-rop", "#as-colors", "#as-edited", "#as-months", "#as-orients", "#as-weekdays",
                    "#as-h1", "#as-h2", "#as-mp1", "#as-mp2", "#as-add1", "#as-add2", "#as-kws", "#as-kwall", "#as-kw", "#as-gps", "#as-faces", "#as-fav", "#as-score", "#as-albums", "#as-map"]
        pg.evaluate("advancedSearch()")
        pg.wait_for_selector("#as-text")
        missing = [c for c in controls if pg.locator(c).count() == 0]
        check("the form has a control for every filter", not missing, missing)
        pg.evaluate("closeModal()")

        n = search(lambda: pg.click('#as-flags [data-x="pick"]'))
        check("form: the Picks flag", n == 1, (n, last["info"]))
        n = search(lambda: (pg.click('#as-flags [data-x="pick"]'), pg.click('#as-flags [data-x="rej"]')))
        check("form: two flags are 'either'", n == 2, n)
        n = search(lambda: (pg.select_option("#as-rop", ">="), pg.select_option("#as-rating", "3")))
        check("form: rating at least 3 stars", n == 2, n)
        n = search(lambda: pg.click('#as-colors [data-x="red"]'))
        check("form: color red", n == 1, n)
        n = search(lambda: pg.fill("#as-text", "beach"))
        check("form: text in any field", n == 2, n)
        n = search(lambda: (pg.fill("#as-kws", "sun, beach"), pg.check("#as-kwall")))
        check("form: keywords, all of them", n == 1, n)
        n = search(lambda: pg.select_option("#as-gps", "has"))
        check("form: with location", n == sum(1 for i in P if byid[i]["lat"] is not None), n)
        n = search(lambda: pg.click("#as-fav"))
        check("form: favorites", n == 1, n)
        n = search(lambda: pg.click(f'#as-albums [data-x="{album}"]'))
        check("form: an album", n == 2, (n, last["info"]))
        n = search(lambda: pg.click(f'#as-months [data-x="{m0}"]'))
        check("form: a month", n == len(months([m0])), n)

        # the search reopens with what was chosen
        pg.evaluate("advancedSearch()")
        pg.wait_for_selector("#as-text")
        check("the form reopens with the month chosen", pg.locator(f'#as-months [data-x="{m0}"].on').count() == 1)
        pg.evaluate("closeModal()")
        check("no script errors", not errs, errs[:2])
        b.close()
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
