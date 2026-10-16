"""UI test of the Google Photos import window: the "Google Photos" source in the import dialog (first the Client ID / secret form, then
the Connect button), the reminder switch in Preferences (off by default; saved), and the reminder notice. No Google is contacted
(the server logic is covered by tools/test_gphotos.py). Throw-away profile.

    py -3.12 tools/test_gphotos_ui.py
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
PORT = 8813
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_gphotos_ui_test_"))
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
    files = [str(p) for p in sorted((ROOT / "tools" / "sandbox" / "photos").iterdir())][:3]
    call("POST", "/api/import-folder", {"paths": files, "keywords": [], "album": None})
    while call("GET", "/api/job/import")["state"] not in ("done", "error"):
        time.sleep(0.3)

    with sync_playwright() as pw:
        b = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)

        # the import window: Google Photos is one of the sources
        pg.evaluate("openImport('gphotos')")
        pg.wait_for_selector("#gp-cid", timeout=10000)
        check("the Google Photos source asks for the Client ID and secret first", pg.is_visible("#gp-sec") and pg.is_visible("#gp-save"))
        check("the Import button waits (nothing to choose from yet)", pg.is_disabled("#im-go"))
        pg.fill("#gp-cid", "abc.apps.googleusercontent.com")
        pg.fill("#gp-sec", "s3cret")
        pg.click("#gp-save")
        pg.wait_for_selector("#gp-connect", timeout=10000)
        i = call("GET", "/api/gphotos")
        check("saved: configured but not connected, and the secret is not shown", i["configured"] and not i["connected"] and "s3cret" not in json.dumps(i), i)
        check("the Connect button is offered; the Choose button is still disabled", pg.is_visible("#gp-connect") and pg.is_disabled("#im-go"))
        pg.click("#im-cancel")

        # Preferences: the reminder is off until switched on, then it is saved
        pg.evaluate("preferences()")
        pg.wait_for_selector("#gp-rem", timeout=10000)
        pg.wait_for_function("document.querySelector('#gp-status').textContent.length > 0", timeout=10000)
        check("the reminder is off by default and its interval is disabled", not pg.is_checked("#gp-rem") and pg.is_disabled("#gp-days"))
        pg.check("#gp-rem")
        pg.wait_for_function("!document.querySelector('#gp-days').disabled", timeout=10000)
        pg.fill("#gp-days", "5")
        pg.dispatch_event("#gp-days", "change")
        t0 = time.time()
        while time.time() - t0 < 10 and call("GET", "/api/gphotos")["reminder_days"] != 5:
            time.sleep(0.2)
        i = call("GET", "/api/gphotos")
        check("switching it on and choosing 5 days is saved", i["reminder"] and i["reminder_days"] == 5, i)
        check("it is not due the moment it is switched on", not call("GET", "/api/background")["gphotos_due"])
        pg.click("#gp-open")
        pg.wait_for_selector("#gp-connect", timeout=10000)
        check("the button in Preferences opens the Google Photos source", True)
        pg.click("#im-cancel")

        # the reminder notice: "Not now" snoozes, "Stop reminding me" switches it off
        pg.evaluate("gpReminderNotice()")
        pg.wait_for_selector("#gr-go", timeout=10000)
        check("the reminder notice offers Import now / Not now / Stop reminding me", pg.is_visible("#gr-later") and pg.is_visible("#gr-off"))
        pg.click("#gr-off")
        t0 = time.time()
        while time.time() - t0 < 10 and call("GET", "/api/gphotos")["reminder"]:
            time.sleep(0.2)
        check("'Stop reminding me' switches the reminder off", not call("GET", "/api/gphotos")["reminder"])
        check("no script errors", not errs, errs)
        b.close()
finally:
    srv.terminate()

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
