"""Test: the trash (Delete key inside the trash deletes for good after asking; restore is separate), the right-click
menu on photos, and the sort options. Throw-away profile, nothing real is touched.

    py -3.12 tools/test_trash_menu.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8779
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_trash_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8"}
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
    files = [str(p) for p in sorted((ROOT / "tools" / "sandbox" / "photos").iterdir()) if p.suffix == ".jpg"]
    call("POST", "/api/import-folder", {"paths": files, "keywords": [], "album": None})
    while call("GET", "/api/job/import")["state"] not in ("done", "error"):
        time.sleep(0.3)
    ph = call("GET", "/api/photos?limit=99")
    n = len(ph)
    check("sample photos imported", n == len(files) and n >= 5, n)

    # ---- server: delete-forever only touches photos that are in the trash
    a, b_, c = ph[0]["id"], ph[1]["id"], ph[2]["id"]
    r = call("POST", "/api/photos/delete-forever", {"ids": [a]})
    check("delete-forever ignores a photo that is NOT in the trash", r["deleted"] == 0 and len(call("GET", "/api/photos?limit=99")) == n, r)
    call("PATCH", "/api/photos", {"ids": [a, b_, c], "trashed": 1})
    check("three photos are in the trash, with the time they were put there",
          len(call("GET", "/api/photos?trashed=1&limit=99")) == 3 and all(p["trashed_at"] for p in call("GET", "/api/photos?trashed=1&limit=99")))
    media = tmp / "lib" / "media"
    before = sum(1 for f in media.rglob("*") if f.is_file())

    with sync_playwright() as pw:
        br = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = br.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)
        ev = pg.evaluate

        # ---- right-click menu on a normal photo
        pg.click(".cell >> nth=0", button="right")
        pg.wait_for_selector("#menu-pop:not(.hidden)")
        items = ev("[...document.querySelectorAll('#menu-pop .mi span:first-child')].map(e => e.textContent)")
        check("right-click on a photo opens a menu with useful commands",
              all(x in items for x in ("Show in Explorer", "Move to Trash", "Flag: Pick", "Add to Quick Collection")), items)
        check("the menu has no 'Restore' outside the trash", "Restore" not in items)
        check("right-click selected the photo", ev("S.sel.size") == 1)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)
        check("Escape closes the menu", ev("document.querySelector('#menu-pop').classList.contains('hidden')"))
        pg.click(".cell >> nth=0", button="right")
        pg.wait_for_selector("#menu-pop:not(.hidden)")
        pg.mouse.click(1450, 12)
        pg.wait_for_timeout(200)
        check("clicking elsewhere closes the menu", ev("document.querySelector('#menu-pop').classList.contains('hidden')"))

        # ---- the trash
        ev("setSource(srcFromKey('trash'))")
        pg.wait_for_function("S.list.length===3")
        pg.click(".cell >> nth=0", button="right")
        pg.wait_for_selector("#menu-pop:not(.hidden)")
        items = ev("[...document.querySelectorAll('#menu-pop .mi span:first-child')].map(e => e.textContent)")
        check("right-click in the trash offers Restore and Delete permanently", "Restore" in items and "Delete permanently" in items, items)
        check("...and not 'Move to Trash'", "Move to Trash" not in items)
        pg.keyboard.press("Escape")

        # Delete key: asks, and "Cancel" keeps everything
        ev("selectOnly(S.list[0].id)")
        pg.keyboard.press("Delete")
        pg.wait_for_selector("#cb-yes")
        txt = ev("document.querySelector('#modal-box').innerText")
        check("Delete in the trash asks before deleting for good", "permanently" in txt.lower() and "cannot be undone" in txt, txt[:90].replace("\n", " "))
        pg.click("#cb-no")
        pg.wait_for_timeout(300)
        check("Cancel keeps the photo in the trash", len(call("GET", "/api/photos?trashed=1&limit=99")) == 3)
        # Delete key does NOT restore any more
        check("Delete did not restore anything", len(call("GET", "/api/photos?limit=99")) == n - 3)
        # confirm -> gone for good (row and file)
        pg.keyboard.press("Delete")
        pg.wait_for_selector("#cb-yes")
        pg.click("#cb-yes")
        pg.wait_for_function("S.list.length===2")
        check("after confirming, the photo is deleted for good", len(call("GET", "/api/photos?trashed=1&limit=99")) == 2 and len(call("GET", "/api/photos?limit=99")) == n - 3)
        after = sum(1 for f in media.rglob("*") if f.is_file())
        check("its file is gone from the media folder", after == before - 1, (before, after))

        # restore via the menu bar command
        ev("selectOnly(S.list[0].id)")
        ev("restoreSelected()")
        pg.wait_for_function("S.list.length===1")
        check("Restore puts it back in the library", len(call("GET", "/api/photos?limit=99")) == n - 2)

        # empty the trash
        ev("setTimeout(emptyTrash, 0)")
        pg.wait_for_selector("#cb-yes")
        pg.click("#cb-yes")
        pg.wait_for_function("S.list.length===0")
        check("Empty the trash deletes what is left", len(call("GET", "/api/photos?trashed=1&limit=99")) == 0 and len(call("GET", "/api/photos?limit=99")) == n - 2)

        # ---- sort options
        ev("setSource(srcFromKey('all'))")
        pg.wait_for_function("S.list.length>0")
        opts = ev("[...document.querySelectorAll('#toolbar select[data-t=sort] option')].map(o => o.value)")
        check("sort offers capture time, name, type, folder, size, dimensions, rating, flag, label, edited",
              all(k in opts for k in ("capture", "import", "name", "ext", "folder", "size", "dims", "rating", "pick", "label", "edited")), opts)
        check("'Date in Trash' is offered only inside the trash", "trashed" not in opts)
        for key in ("name", "size", "dims", "ext", "folder", "edited"):
            pg.select_option("#toolbar select[data-t=sort]", key)
            pg.wait_for_timeout(150)
            lst = ev("S.list.map(p => ({n: p.filename, b: p.bytes, w: p.width, h: p.height}))")
            if key == "name":
                ok = [x["n"].lower() for x in lst] == sorted(x["n"].lower() for x in lst) and ev("S.asc")
            elif key == "size":
                ok = [x["b"] for x in lst] == sorted((x["b"] for x in lst), reverse=True) and not ev("S.asc")
            elif key == "dims":
                ok = [x["w"] * x["h"] for x in lst] == sorted((x["w"] * x["h"] for x in lst), reverse=True)
            else:
                ok = len(lst) == n - 2
            check(f"sort by {key} orders correctly (text A→Z, numbers biggest first)", ok)
        check("no JavaScript errors", not errs, errs[:2])
        br.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=15)
    except Exception:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
