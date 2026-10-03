"""UI test of pre-releases in the update dialogs, against a mock GitHub: with tester mode ON the offered pre-release gets a big
PRE-RELEASE banner; with tester mode OFF the automatic check stays silent but a MANUAL check still says a pre-release exists
(and only opens its page -- it is never installed from there); a stable update never gets the banner.

    py -3.12 tools/test_prerelease_ui.py       (set PHOTAG_TEST_BROWSER to a Chromium executable to use it instead of Edge)
"""
import json
import os
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
PORT, MOCK = 8797, 8798
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_preui_"))
res = []
PRE = {"tag_name": "v9.0.0-beta.1", "body": "## Pre notes\n- try the new thing", "html_url": "https://example.invalid/pre", "published_at": "2026-10-03T00:00:00Z", "assets": [], "prerelease": True}
OLD = {"tag_name": "v1.0.0", "body": "old", "html_url": "https://example.invalid/old", "published_at": "2026-01-01T00:00:00Z", "assets": [], "prerelease": False}
NEW = {"tag_name": "v8.0.0", "body": "## Stable notes\n- stable thing", "html_url": "https://example.invalid/new", "published_at": "2026-10-01T00:00:00Z", "assets": [], "prerelease": False}
STATE = {"pre": True, "stable": OLD}


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


class Mock(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path.endswith("/releases/latest"):
            body = STATE["stable"]
        elif path.endswith("/releases"):
            body = ([PRE] if STATE["pre"] else []) + [STATE["stable"]]
        elif "/releases/tags/" in path:
            body = PRE if "beta" in path else STATE["stable"]
        else:
            body = {}
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(json.dumps(body).encode())

    def log_message(self, *a):
        pass


mock = HTTPServer(("127.0.0.1", MOCK), Mock)
threading.Thread(target=mock.serve_forever, daemon=True).start()
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"), "HOME": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_UPDATE_API": f"http://127.0.0.1:{MOCK}", "PHOTAG_BACKUP_START_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib"):
    (tmp / d).mkdir()


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
    call("POST", "/api/import-folder", {"paths": [str(ROOT / "tools" / "sandbox" / "photos" / "paris.jpg")], "keywords": [], "album": None})
    time.sleep(2)

    # ---- the server side, tester mode off
    o = call("GET", "/api/update/check?force=1")
    check("API, tester off, plain check: no pre-release is mentioned", o["pre"] is None and not o["available"], o)
    o = call("GET", "/api/update/check?force=1&pre=1")
    check("API, tester off, manual check (pre=1): the pre-release is reported", o["pre"] and o["pre"]["latest"] == "9.0.0-beta.1" and not o["available"], o["pre"])

    with sync_playwright() as pw:
        exe = os.environ.get("PHOTAG_TEST_BROWSER")
        br = pw.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"]) if exe else pw.chromium.launch(channel="msedge", headless=True)
        pg = br.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)
        ev = pg.evaluate
        pg.wait_for_timeout(4500)                                   # the start-up check runs shortly after boot
        hidden = lambda: ev("document.querySelector('#modal').classList.contains('hidden')")
        text = lambda: ev("document.querySelector('#modal-box').innerText")
        check("tester OFF, automatic check: no window (the pre-release is not offered)", hidden())

        # ---- tester OFF, manual check
        ev("updateCheck(true)")
        pg.wait_for_selector(".pre-banner", timeout=10000)
        t = text()
        check("tester OFF, MANUAL check: a pre-release window appears", "Pre-release available" in t and "9.0.0-beta.1" in t, t[:80].replace("\n", " "))
        check("...with the big PRE-RELEASE banner", "PRE-RELEASE" in t)
        fs = ev("parseFloat(getComputedStyle(document.querySelector('.pre-banner')).fontSize)")
        w = ev("document.querySelector('.pre-banner').getBoundingClientRect().width")
        check("...really big (font 20px+, full width of the dialog)", fs >= 20 and w > 250, (fs, w))
        check("...says it is a test version and explains Tester mode", "test version" in t and "Tester mode" in t)
        check("...shows the pre-release's notes", "try the new thing" in t)
        check("...and cannot be installed from here: only 'Open the release page' (no Update now / Skip)", ev("!document.querySelector('#up-go') && !document.querySelector('#up-skip')") and "Open the release page" in t)
        if os.environ.get("PHOTAG_TEST_SHOTS"):
            pg.screenshot(path=str(Path(os.environ["PHOTAG_TEST_SHOTS"]) / "prerelease_manual.png"))
        pg.click("#pre-close")
        check("Close closes it", hidden())
        check("(a manual check does not change the tester-mode setting)", call("GET", "/api/update/beta") == {"on": False})

        # ---- tester ON: the pre-release is the offer itself
        call("POST", "/api/update/beta", {"on": True})
        ev("updateCheck(true)")
        pg.wait_for_selector("#up-go", timeout=10000)
        t = text()
        check("tester ON: the update window shows the PRE-RELEASE banner", ev("!!document.querySelector('.pre-banner')") and "PRE-RELEASE" in t)
        check("...titled 'Pre-release available' with the version", "Pre-release available" in t and "9.0.0-beta.1" in t)
        check("...and keeps its Skip / Later buttons", ev("!!document.querySelector('#up-skip') && !!document.querySelector('#up-later')"))
        if os.environ.get("PHOTAG_TEST_SHOTS"):
            pg.screenshot(path=str(Path(os.environ["PHOTAG_TEST_SHOTS"]) / "prerelease_tester.png"))
        pg.click("#up-later")
        # the automatic check (tester ON) offers it too
        ev("pref.set('updateCheckedAt', Date.now() - 25*3600*1000)")
        ev("autoUpdateTick()")
        pg.wait_for_selector(".pre-banner", timeout=10000)
        check("tester ON: the automatic check offers it with the banner too", "9.0.0-beta.1" in text())
        ev("closeModal()")

        # ---- a stable update never gets the banner
        call("POST", "/api/update/beta", {"on": False})
        STATE["stable"] = NEW
        ev("updateCheck(true)")
        pg.wait_for_selector("#up-go", timeout=10000)
        t = text()
        check("a STABLE update: normal window, no banner", "Update available" in t and "8.0.0" in t and not ev("!!document.querySelector('.pre-banner')"))
        check("...and, tester OFF, it still mentions the newer pre-release (manual check) without offering it", "A newer pre-release is also available" in t and "9.0.0-beta.1" in t)
        ev("closeModal()")

        # ---- nothing at all
        STATE["pre"], STATE["stable"] = False, OLD
        ev("updateCheck(true)")
        pg.wait_for_timeout(1500)
        check("no pre-release and no update: just the 'latest version' message (no window)", hidden() and "latest version" in ev("document.querySelector('#toast')?.innerText || document.body.innerText"))
        check("no JavaScript errors", not errs, errs[:3])
        br.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
