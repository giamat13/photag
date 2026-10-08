"""Test: the Preferences section for background mode and the one-time offer to make photag the picture viewer, in a real browser
(Windows-only answers are faked at the network level). Edge on Windows, or PHOTAG_TEST_BROWSER=<chromium>.

    py -3.12 tools/test_background_ui.py
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
PORT = 8794
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_bgui_"))
for d in ("a", "l", "h"):
    (tmp / d).mkdir()
env = {**os.environ, "APPDATA": str(tmp / "a"), "LOCALAPPDATA": str(tmp / "l"), "USERPROFILE": str(tmp / "h"), "HOME": str(tmp / "h"), "PYTHONIOENCODING": "utf-8"}
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    posted = []
    state = {"supported": True, "keep": True, "autostart": False, "running": True}
    with sync_playwright() as pw:
        exe = os.environ.get("PHOTAG_TEST_BROWSER")
        br = pw.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"]) if exe else pw.chromium.launch(channel="msedge", headless=True)
        pg = br.new_page(viewport={"width": 1280, "height": 860})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        def tray(route):
            r = route.request
            if r.method == "POST":
                body = json.loads(r.post_data or "{}")
                posted.append(("tray", body))
                state.update(body)
                if state["autostart"]:
                    state["keep"] = True
            route.fulfill(status=200, content_type="application/json", body=json.dumps(state))

        def fileassoc(route):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"supported": True, "on": True}))

        def defaults(route):
            posted.append(("default-apps", None))
            route.fulfill(status=200, content_type="application/json", body="{}")

        def lang(route):
            posted.append(("ui-lang", json.loads(route.request.post_data or "{}")))
            route.fulfill(status=200, content_type="application/json", body="{}")

        pg.route("**/api/tray", tray)
        pg.route("**/api/fileassoc", fileassoc)
        pg.route("**/api/fileassoc/default-apps", defaults)
        pg.route("**/api/ui-lang", lang)
        pg.goto(APP + "/")
        pg.wait_for_selector("#grid, .grid, body", timeout=20000)
        pg.wait_for_function("document.querySelector('#modal-box') && document.querySelector('#modal-box').textContent.includes('picture viewer')", timeout=20000)
        check("the first start offers to make photag the picture viewer", "Make photag your picture viewer?" in pg.inner_text("#modal-box"))
        check("the window told the server its language (for the notifications)", any(k == "ui-lang" and v.get("lang") == "en" for k, v in posted), posted)
        pg.click("#vo-yes")
        pg.wait_for_timeout(300)
        check("'Yes' opens Windows' Default apps and closes the offer", any(k == "default-apps" for k, _ in posted) and pg.is_hidden("#modal"))
        stored = pg.evaluate("localStorage.getItem('pm.viewerOffer')")
        check("it is offered once only", stored == "true", stored)
        pg.reload()
        pg.wait_for_timeout(8500)
        check("not again on the next start", pg.is_hidden("#modal"))

        pg.evaluate("preferences()")
        pg.wait_for_selector("#bg-keep", timeout=10000)
        check("Preferences: the two background switches with the saved state", pg.is_checked("#bg-keep") and not pg.is_checked("#bg-auto"))
        pg.click("#bg-auto")
        pg.wait_for_timeout(300)
        check("start at sign-in is sent (and keeps the icon switched on)", ("tray", {"autostart": True}) in posted and pg.is_checked("#bg-keep") and pg.is_checked("#bg-auto"), posted)
        pg.click("#bg-keep")
        pg.wait_for_timeout(300)
        check("switching the icon off also switches off start at sign-in", ("tray", {"keep": False, "autostart": False}) in posted and not pg.is_checked("#bg-auto"), posted)
        check("no JavaScript errors", not errs, errs[:2])
        br.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()
shutil.rmtree(tmp, ignore_errors=True)
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
