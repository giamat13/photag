"""UI test of issue #27: editing the keywords / caption and pressing Backspace must never ask "delete the photo?" -- not even after
Enter, when the field has lost the focus. Only the Delete key moves the selected photo to the trash. Throw-away profile.

    py -3.12 tools/test_delete_key.py
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
PORT = 8789
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_delkey_test_"))
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
    files = [str(p) for p in sorted((ROOT / "tools" / "sandbox" / "photos").iterdir()) if p.suffix.lower() == ".jpg"][:3]
    call("POST", "/api/import-folder", {"paths": files, "keywords": [], "album": None})
    while call("GET", "/api/job/import")["state"] not in ("done", "error"):
        time.sleep(0.3)
    n_all = len(call("GET", "/api/photos?limit=99"))

    with sync_playwright() as pw:
        b = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)
        pg.click(".cell >> nth=0")
        pg.wait_for_timeout(500)
        modal_open = lambda: pg.evaluate("!document.querySelector('#modal').classList.contains('hidden')")

        # type in the caption, press Enter (the field loses the focus), then Backspace
        pg.click("#m-desc")
        pg.keyboard.type("holiday by the sea")
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(300)
        check("Enter in the caption leaves the field (the situation of the bug)", pg.evaluate("document.activeElement.id") != "m-desc", pg.evaluate("document.activeElement.tagName"))
        pg.keyboard.press("Backspace")
        pg.wait_for_timeout(500)
        check("Backspace then does NOT ask to delete the photo", not modal_open())
        check("...and the photo is still in the library", len(call("GET", "/api/photos?limit=99")) == n_all)

        # the same in the keywords box
        pg.evaluate("document.querySelector('.pnl[data-p=kwing]').classList.remove('shut')")
        pg.click("#kw-add")
        pg.keyboard.type("sea, sun")
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(500)
        for _ in range(3):
            pg.keyboard.press("Backspace")
        pg.wait_for_timeout(500)
        check("Backspace after adding keywords does not ask to delete the photo either", not modal_open() and len(call("GET", "/api/photos?limit=99")) == n_all)

        # Delete still works as before: it moves the selected photo to the trash
        pg.click(".cell >> nth=1")
        pg.wait_for_timeout(300)
        pg.keyboard.press("Delete")
        pg.wait_for_timeout(1000)
        left = len(call("GET", "/api/photos?limit=99"))
        check("the Delete key still removes the selected photo (to the trash / after asking)", left == n_all - 1 or modal_open(), (left, n_all, modal_open()))
        check("no script errors", not errs, errs[:2])
        b.close()
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
