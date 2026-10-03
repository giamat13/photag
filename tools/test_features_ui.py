"""UI test of: right-click "Add to Collection", Ctrl+Z / Ctrl+Y (ratings, flags, labels, trash), Advanced Search with saved
searches, the trash period setting, the backup check button and the automatic-import settings. Throw-away profile.

    py -3.12 tools/test_features_ui.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8782
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_features_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib", "watch"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read() or b"{}")


def photos(**q):
    return call("GET", "/api/photos?limit=999" + "".join(f"&{k}={v}" for k, v in q.items()))


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
    files = [str(p) for p in sorted((ROOT / "tools" / "sandbox" / "photos").iterdir())]
    call("POST", "/api/import-folder", {"paths": files, "keywords": [], "album": None})
    while call("GET", "/api/job/import")["state"] not in ("done", "error"):
        time.sleep(0.3)
    n_all = len(photos())
    n_video = sum(1 for p in photos() if p["is_video"])
    check("sample photos imported", n_all == len(files) and n_video == 1, (n_all, n_video))
    call("POST", "/api/albums", {"name": "Trip", "ids": []})
    trip = next(a["id"] for a in call("GET", "/api/albums") if a["name"] == "Trip")

    with sync_playwright() as pw:
        b = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)
        pg.wait_for_timeout(800)

        # ---- 1. right-click -> Add to Collection (fly-out with the collections and "New Collection...")
        pg.click(".cell >> nth=0", button="right")
        pg.wait_for_selector("#menu-pop:not(.hidden)")
        check("right-click menu has 'Add to Collection'", pg.locator("#menu-pop .mi.sub").count() == 1, pg.inner_text("#menu-pop")[:60].replace("\n", " "))
        pg.hover("#menu-pop .mi.sub")
        pg.wait_for_selector("#menu-sub:not(.hidden)")
        sub = pg.inner_text("#menu-sub")
        check("the fly-out lists 'New Collection...' and the existing collections", "New Collection..." in sub and "Trip" in sub, sub.replace("\n", " | "))
        first_id = int(pg.evaluate("document.querySelector('.cell').dataset.id"))
        pg.click("#menu-sub .mi:has-text('Trip')")
        pg.wait_for_timeout(600)
        got = [p["id"] for p in photos(album=trip)]
        check("clicking a collection adds the photo to it", got == [first_id], got)
        check("the menu closes", pg.evaluate("document.querySelector('#menu-pop').classList.contains('hidden') && document.querySelector('#menu-sub').classList.contains('hidden')"))
        # the same on several selected photos
        pg.click(".cell >> nth=1")
        pg.click(".cell >> nth=2", modifiers=["Shift"])
        pg.click(".cell >> nth=2", button="right")
        pg.hover("#menu-pop .mi.sub")
        pg.click("#menu-sub .mi:has-text('Trip')")
        pg.wait_for_timeout(600)
        check("works for a multi-photo selection", len(photos(album=trip)) == 3, len(photos(album=trip)))

        # ---- 2. Undo / Redo
        def by_id(i):
            return next(p for p in photos() if p["id"] == i)

        pg.click(".cell >> nth=0")
        pid = int(pg.evaluate("S.act"))
        pg.keyboard.press("5")
        pg.wait_for_timeout(500)
        check("rating 5 saved", by_id(pid)["rating"] == 5)
        pg.keyboard.press("Control+z")
        pg.wait_for_timeout(500)
        check("Ctrl+Z undoes the rating", by_id(pid)["rating"] == 0, by_id(pid)["rating"])
        pg.keyboard.press("Control+y")
        pg.wait_for_timeout(500)
        check("Ctrl+Y redoes it", by_id(pid)["rating"] == 5)
        pg.keyboard.press("Control+z")
        pg.wait_for_timeout(400)

        pg.keyboard.press("p")
        pg.wait_for_timeout(400)
        pg.keyboard.press("6")
        pg.wait_for_timeout(400)
        check("flag + label set", by_id(pid)["flag"] == 1 and by_id(pid)["label"] == "red")
        pg.keyboard.press("Control+z")
        pg.wait_for_timeout(500)
        check("Ctrl+Z undoes the label first (last action first)", by_id(pid)["label"] in (None, "") and by_id(pid)["flag"] == 1, (by_id(pid)["label"], by_id(pid)["flag"]))
        pg.keyboard.press("Control+z")
        pg.wait_for_timeout(500)
        check("...then the flag", by_id(pid)["flag"] == 0)
        pg.keyboard.press("Control+Shift+z")
        pg.wait_for_timeout(500)
        check("Ctrl+Shift+Z redoes too", by_id(pid)["flag"] == 1)
        pg.keyboard.press("Control+z")
        pg.wait_for_timeout(400)

        # a selection of several photos goes back to each photo's own previous value
        pg.click(".cell >> nth=3")
        pg.keyboard.press("2")
        pg.wait_for_timeout(400)
        pg.keyboard.press("Control+a")
        pg.keyboard.press("4")
        pg.wait_for_timeout(700)
        check("all photos rated 4", all(p["rating"] == 4 for p in photos()))
        pg.keyboard.press("Control+z")
        pg.wait_for_timeout(900)
        ratings = sorted(p["rating"] for p in photos())
        check("undo restores every photo's own previous rating (one had 2, the others 0)", ratings == [0] * (n_all - 1) + [2], ratings)
        pg.keyboard.press("Control+z")
        pg.wait_for_timeout(600)
        pg.keyboard.press("Control+d")

        # trash and undo
        pg.click(".cell >> nth=0")
        tid = int(pg.evaluate("S.act"))
        pg.keyboard.press("Delete")
        pg.wait_for_selector("#cb-yes")
        pg.click("#cb-yes")
        pg.wait_for_timeout(800)
        check("Delete moved the photo to the trash", len(photos()) == n_all - 1 and len(photos(trashed=1)) == 1)
        pg.keyboard.press("Control+z")
        pg.wait_for_timeout(1000)
        check("Ctrl+Z brings it back from the trash", len(photos()) == n_all and len(photos(trashed=1)) == 0)
        check("...and selects it again", int(pg.evaluate("S.act")) == tid and pg.evaluate("S.sel.has(S.act)"))
        pg.keyboard.press("Control+Shift+z")
        pg.wait_for_timeout(1000)
        check("redo trashes it again", len(photos(trashed=1)) == 1)
        pg.keyboard.press("Control+z")
        pg.wait_for_timeout(800)
        # nothing left to redo/undo gives a message, not an error
        for _ in range(30):
            pg.keyboard.press("Control+z")
        pg.wait_for_timeout(500)
        check("undo with an empty history only shows a message", "undo" in pg.inner_text("#toast").lower() and not errs, pg.inner_text("#toast"))

        # typing in a field keeps the browser's own undo (no photo is touched)
        before = [p["rating"] for p in photos()]
        pg.keyboard.press("Control+f")
        pg.keyboard.type("abc")
        pg.keyboard.press("Control+z")
        pg.wait_for_timeout(300)
        check("Ctrl+Z inside a text field does not touch photos", [p["rating"] for p in photos()] == before)
        pg.keyboard.press("Escape")
        pg.evaluate("clearFilters()")

        # ---- 3. Advanced search
        pg.keyboard.press("Control+Shift+f")
        pg.wait_for_selector("#as-go")
        check("Ctrl+Shift+F opens Advanced Search with date, file type, size and a map", all(pg.locator(s).count() for s in ("#as-from", "#as-to", "#as-kind", "#as-exts button", "#as-min", "#as-max", "#as-map .leaflet-pane")))
        pg.click("#as-go")
        check("an empty search asks for a condition", "condition" in pg.inner_text("#toast").lower())
        pg.select_option("#as-kind", "video")
        pg.click("#as-save")
        pg.wait_for_selector("#pb-in")
        pg.fill("#pb-in", "Only videos")
        pg.click("#pb-ok")
        pg.wait_for_timeout(1200)
        check("a saved search shows just the videos", pg.evaluate("S.list.length") == n_video and pg.evaluate("S.src.kind") == "search", pg.evaluate("S.list.length"))
        saved = call("GET", "/api/searches")
        check("the search is stored in the catalog", len(saved) == 1 and saved[0]["name"] == "Only videos" and saved[0]["criteria"]["kind"] == "video", saved)
        check("it is listed under Saved Searches in the left panel with its count", "Only videos" in pg.inner_text("#p-colls") and "Saved Searches" in pg.inner_text("#p-colls"))

        sp = pg.evaluate("""()=>{
          const all = S.all, jer = all.find(p=>p.filename==='jerusalem.jpg'), tlv = all.find(p=>p.filename==='telaviv_1.jpg'), par = all.find(p=>p.filename==='paris.jpg');
          const n = c => all.filter(searchPass(c)).length;
          const near = n({place:{lat:jer.lat, lng:jer.lng, km:5}});
          const wide = n({place:{lat:jer.lat, lng:jer.lng, km:100}});
          const world = n({place:{lat:jer.lat, lng:jer.lng, km:5000}});
          const day = new Date(par.taken_at*1000), d = x=>x.toISOString().slice(0,10);
          return {near, wide, world, withGps: all.filter(p=>p.lat!=null).length,
                  onDay: all.filter(searchPass({from:d(day), to:d(day)})).every(p=>d(new Date(p.taken_at*1000))===d(day)) ,
                  noDay: n({from:'1990-01-01', to:'1990-01-02'}),
                  big: n({minMB:0.05}), small: n({maxMB:0.05}), jpg: n({exts:['JPG']}), mp4: n({exts:['MP4']}), none: n({})}
        }""")
        check("place: only photos within the radius (5 km around Jerusalem)", sp["near"] >= 1 and sp["near"] < sp["world"], sp)
        check("place: a bigger radius includes more, and photos without GPS never match", sp["wide"] >= sp["near"] and sp["world"] <= sp["withGps"], sp)
        check("dates: a range matches that day only; an empty range matches nothing", sp["onDay"] and sp["noDay"] == 0, sp)
        check("size: min and max split the library (every photo is on one side)", sp["big"] + sp["small"] >= n_all and sp["big"] > 0 and sp["small"] > 0, sp)
        check("file type: JPG and MP4 select by extension", sp["jpg"] == n_all - n_video and sp["mp4"] == n_video, sp)

        pg.reload()
        pg.wait_for_selector(".cell, #v-empty", timeout=20000)
        pg.wait_for_timeout(800)
        pg.click("#p-colls [data-src^='search:']")
        pg.wait_for_timeout(800)
        check("after a restart the saved search is still there and works", pg.evaluate("S.list.length") == n_video, pg.evaluate("S.list.length"))
        pg.keyboard.press("Control+Shift+f")
        pg.wait_for_selector("#as-go")
        check("opening Advanced Search from a saved search shows its conditions", pg.input_value("#as-kind") == "video")
        pg.click("#as-clear")
        pg.wait_for_timeout(500)
        pg.hover("#p-colls [data-src^='search:']")
        pg.click("#p-colls [data-delsearch]")
        pg.wait_for_selector("#cb-yes")
        pg.click("#cb-yes")
        pg.wait_for_timeout(700)
        check("a saved search can be deleted (the photos stay)", call("GET", "/api/searches") == [] and len(photos()) == n_all)

        # ---- 4. trash period
        st = call("GET", "/api/status")
        check("the trash period defaults to 60 days", st["trash_days"] == 60, st["trash_days"])
        pg.evaluate("catalogSettings()")
        pg.wait_for_selector("#trash-days")
        pg.fill("#trash-days", "30")
        pg.press("#trash-days", "Tab")
        pg.wait_for_timeout(700)
        check("the period can be changed in Catalog Settings", call("GET", "/api/status")["trash_days"] == 30)
        pg.keyboard.press("Escape")
        # a photo that has been in the trash for 10 days
        victim = photos()[0]["id"]
        call("PATCH", "/api/photos", {"ids": [victim], "trashed": 1})
        con = sqlite3.connect(tmp / "lib" / "catalog.db")
        con.execute("UPDATE photos SET trashed_at=? WHERE id=?", (int(time.time()) - 10 * 86400, victim))
        con.commit(); con.close()
        check("preview: a 10-day-old item is not affected by 30 days", call("GET", "/api/trash/preview?days=30")["n"] == 0 and call("GET", "/api/trash/preview?days=5")["n"] == 1)
        pg.evaluate("catalogSettings()")
        pg.wait_for_selector("#trash-days")
        pg.fill("#trash-days", "5")
        pg.press("#trash-days", "Tab")
        pg.wait_for_selector("#cb-yes")
        check("shortening the period asks before deleting what is already older", "1" in pg.inner_text("#modal-box") and "permanently" in pg.inner_text("#modal-box"))
        pg.click("#cb-no")
        pg.wait_for_timeout(500)
        check("cancelling keeps the old period and deletes nothing", call("GET", "/api/status")["trash_days"] == 30 and len(photos(trashed=1)) == 1)
        pg.evaluate("catalogSettings()")
        pg.wait_for_selector("#trash-days")
        pg.fill("#trash-days", "5")
        pg.press("#trash-days", "Tab")
        pg.wait_for_selector("#cb-yes")
        pg.click("#cb-yes")
        pg.wait_for_timeout(1200)
        check("confirming saves it and deletes the expired item for good", call("GET", "/api/status")["trash_days"] == 5 and len(photos(trashed=1)) == 0 and len(photos()) == n_all - 1)
        pg.keyboard.press("Escape")
        check("the message in Move to Trash uses the new period", pg.evaluate("S.status.trash_days") == 5)

        # ---- 5. backup check button
        call("POST", "/api/backup/run")
        while call("GET", "/api/job/backup")["state"] not in ("done", "error"):
            time.sleep(0.3)
        pg.evaluate("backupDialog()")
        pg.wait_for_selector("#bk-check")
        pg.click("#bk-check")
        for _ in range(60):
            if call("GET", "/api/job/backupcheck")["state"] == "done":
                break
            time.sleep(0.3)
        h = call("GET", "/api/backup")["health"]["verify"]
        check("'Check the backup' checks the newest backup and records a good result", h and h["ok"] and h["by"] == "manual", h)
        pg.wait_for_timeout(600)
        pg.evaluate("backupDialog()")
        pg.wait_for_selector("#bk-health")
        check("the backup window shows the last check", "no problems" in pg.inner_text("#bk-health"), pg.inner_text("#bk-health")[:120])
        pg.keyboard.press("Escape")

        # ---- 6. automatic import settings
        call("POST", "/api/auto-import", {"enabled": True, "folder": str(tmp / "watch")})
        pg.evaluate("preferences()")
        pg.wait_for_selector("#ai-on")
        check("Preferences shows the automatic import switch and the folder", pg.is_checked("#ai-on") and pg.input_value("#ai-folder") == str(tmp / "watch"))
        pg.uncheck("#ai-on")
        pg.wait_for_timeout(600)
        check("switching it off in Preferences is saved", call("GET", "/api/auto-import")["enabled"] is False)
        pg.keyboard.press("Escape")

        check("no JavaScript errors", not errs, errs[:2])
        b.close()
finally:
    srv.terminate()
    try:
        srv.wait(10)
    except Exception:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
