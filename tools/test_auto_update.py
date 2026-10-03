"""Test: the automatic update check (at start-up and once a day while the app is open), against a mock GitHub.

    py -3.12 tools/test_auto_update.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PORT, MOCK = 8781, 8782
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_auto_update_"))
res = []
STATE = {"tag": "v1.0.0", "hits": 0}


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


class Mock(BaseHTTPRequestHandler):
    def do_GET(self):
        STATE["hits"] += 1
        one = {"tag_name": STATE["tag"], "body": "## Mock release\n- something new", "html_url": "https://example.invalid/r",
               "published_at": "2026-10-02T00:00:00Z", "assets": []}
        # /releases (plural, used for "what's new across several skipped versions") returns a list; /releases/latest returns one object
        path = self.path.split("?", 1)[0].rstrip("/")
        body = json.dumps([one] if path.endswith("/releases") else one).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


mock = HTTPServer(("127.0.0.1", MOCK), Mock)
threading.Thread(target=mock.serve_forever, daemon=True).start()

# _notes_since: several versions newer than the running one -> all their notes combined, not just the latest's
NOTES_PORT = 8783
RELEASES = [{"tag_name": "v3.0.0", "body": "three"}, {"tag_name": "v2.0.0", "body": "two"}, {"tag_name": "v1.0.0", "body": "one"}]


class NotesMock(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(RELEASES).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


notes_srv = HTTPServer(("127.0.0.1", NOTES_PORT), NotesMock)
threading.Thread(target=notes_srv.serve_forever, daemon=True).start()
os.environ["PHOTAG_UPDATE_API"] = f"http://127.0.0.1:{NOTES_PORT}"
from app import updater as _updater
notes = _updater._notes_since("0.5.0")
check("_notes_since combines notes of every skipped version, not just the latest", "three" in notes and "two" in notes and "one" in notes, notes[:80].replace("\n", " "))
notes_srv.shutdown()
del os.environ["PHOTAG_UPDATE_API"]
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_UPDATE_API": f"http://127.0.0.1:{MOCK}"}
for d in ("appdata", "local", "home", "lib"):
    (tmp / d).mkdir()
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    r = urllib.request.Request(APP + "/api/settings/library", data=json.dumps({"path": str(tmp / "lib")}).encode(), method="POST", headers={"Content-Type": "application/json"})
    urllib.request.urlopen(r)
    src = ROOT / "tools" / "sandbox" / "photos"
    r = urllib.request.Request(APP + "/api/import-folder", data=json.dumps({"paths": [str(src / "paris.jpg")], "keywords": [], "album": None}).encode(), method="POST", headers={"Content-Type": "application/json"})
    urllib.request.urlopen(r)
    time.sleep(2)

    with sync_playwright() as pw:
        br = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = br.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)
        ev = pg.evaluate
        pg.wait_for_timeout(4500)                                   # the start-up check runs 2.5 s after boot
        hidden = lambda: ev("document.querySelector('#modal').classList.contains('hidden')")
        check("start-up check: no window when the app is up to date", hidden())
        check("start-up check remembered when it ran", ev("pref.get('updateCheckedAt', 0)") > time.time() * 1000 - 60000)

        # a newer release appears; less than a day since the last check: nothing happens
        STATE["tag"] = "v99.9.9"
        hits = STATE["hits"]
        ev("autoUpdateTick()"); pg.wait_for_timeout(800)
        check("less than a day since the last check: no request, no window", hidden() and STATE["hits"] == hits)

        # more than a day: the check runs and the window offers the update
        ev("pref.set('updateCheckedAt', Date.now() - 25*3600*1000)")
        ev("autoUpdateTick()")
        pg.wait_for_selector("#up-skip", timeout=8000)
        txt = ev("document.querySelector('#modal-box').innerText")
        check("a day later the update window appears by itself", "99.9.9" in txt and "Mock release" in txt, txt[:60].replace("\n", " "))
        check("...and the check time was renewed", ev("pref.get('updateCheckedAt', 0)") > time.time() * 1000 - 60000)
        ev("closeModal()")

        # never on top of another dialog; tried again at the next tick
        ev("pref.set('updateCheckedAt', Date.now() - 25*3600*1000)")
        ev("backupDialog()"); pg.wait_for_timeout(600)
        hits = STATE["hits"]
        ev("autoUpdateTick()"); pg.wait_for_timeout(600)
        check("another dialog is open: the check waits (no request)", STATE["hits"] == hits and "99.9.9" not in ev("document.querySelector('#modal-box').innerText"))
        ev("closeModal()")
        ev("autoUpdateTick()")
        pg.wait_for_selector("#up-skip", timeout=8000)
        check("once the dialog is closed the next tick shows the update", True)
        # "skip this version": never shown again automatically
        pg.click("#up-skip"); pg.wait_for_timeout(500)
        ev("pref.set('updateCheckedAt', Date.now() - 25*3600*1000)")
        ev("autoUpdateTick()"); pg.wait_for_timeout(1200)
        check("a skipped version does not pop up again", hidden())

        # turned off in the preferences: no check at all
        ev("pref.set('autoUpdate', false); pref.set('updateCheckedAt', Date.now() - 25*3600*1000)")
        hits = STATE["hits"]
        ev("autoUpdateTick()"); pg.wait_for_timeout(800)
        check("switched off in the preferences: no request", STATE["hits"] == hits and hidden())
        ev("preferences()"); pg.wait_for_selector("#pf-upd")
        check("the preferences window has the switch (off now)", not ev("document.querySelector('#pf-upd').checked"))
        pg.click("#pf-upd")
        check("switching it on is remembered", ev("pref.get('autoUpdate', false)") is True)
        check("no JavaScript errors", not errs, errs[:2])
        br.close()
finally:
    srv.terminate()
    mock.shutdown()
    try:
        srv.wait(timeout=15)
    except Exception:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
