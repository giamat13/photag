"""UI test of issue #28: two jobs at the same time (for example AI tagging and a backup) are both shown, one row each with its own
bar, instead of one indicator that jumps from the one to the other. Rows are updated in place; a finished job's row goes away.
Throw-away profile; the jobs are faked in the page (the real ones are tested elsewhere).

    py -3.12 tools/test_activity_rows.py
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
PORT = 8791
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_activity_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


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
    urllib.request.urlopen(r, timeout=30).read()

    with sync_playwright() as pw:
        b = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector("#activity", state="attached", timeout=20000)
        pg.wait_for_timeout(800)
        rows = lambda: pg.evaluate("[...document.querySelectorAll('#activity .act-row')].map(r => ({job: r.dataset.job, text: r.querySelector('.act-label').textContent, w: r.querySelector('i').style.width}))")
        hidden = lambda: pg.evaluate("document.querySelector('#activity').classList.contains('hidden')")
        check("nothing is running: the indicator is hidden", hidden() and not rows())

        # two jobs running at once (their state comes from the program in real life; here the page is told directly)
        pg.evaluate("ACT.set('aitag', {name:'aitag', label:'AI tagging', pct:40, title:'AI tagging: 40 of 100'}); ACT.set('backup', {name:'backup', label:'Backup', pct:75, title:'Backup'}); renderActivity()")
        got = rows()
        check("two jobs at once: two rows are shown", len(got) == 2 and not hidden(), got)
        check("...each with its own name and percentage", {g["job"]: g["text"] for g in got} == {"aitag": "AI tagging · 40%", "backup": "Backup · 75%"}, got)
        check("...and its own bar", {g["job"]: g["w"] for g in got} == {"aitag": "40%", "backup": "75%"}, got)

        # the first one moves on: its row changes, the other one does not jump
        keep = pg.evaluate("document.querySelectorAll('#activity .act-row')[1]")  # a handle must not be returned; just check text next
        pg.evaluate("ACT.set('aitag', {name:'aitag', label:'AI tagging', pct:55, title:'AI tagging: 55 of 100'}); renderActivity()")
        got = rows()
        check("one job moves on: its row updates and the other row stays as it was", {g["job"]: g["text"] for g in got} == {"aitag": "AI tagging · 55%", "backup": "Backup · 75%"}, got)
        check("...in the same order (no jumping between them)", [g["job"] for g in got] == ["aitag", "backup"], got)

        # a job without a total is shown as a moving bar, not as 0%
        pg.evaluate("ACT.set('backup', {name:'backup', label:'Backup', pct:null, title:'Backup'}); renderActivity()")
        check("a job with no total shows a moving bar", pg.evaluate("document.querySelectorAll('#activity .act-bar')[1].classList.contains('indet')"))

        # clicking a row opens that job's own screen
        pg.evaluate("window.__opened = []; window.__stacks = []; jobScreenOpen = (n, l) => { window.__opened.push(n); window.__stacks.push(new Error().stack.split(String.fromCharCode(10)).slice(1, 4).join(' | ')); }; 0")       # (; 0: evaluate calls a function it gets back)
        pg.click("#activity .act-row >> nth=1")
        check("clicking a row opens the screen of that job", pg.evaluate("window.__opened") == ["backup"], (pg.evaluate("window.__opened"), pg.evaluate("window.__stacks")))

        # a job ends: only its row goes
        pg.evaluate("ACT.delete('aitag'); renderActivity()")
        got = rows()
        check("a job ends: its row goes and the other stays", [g["job"] for g in got] == ["backup"] and not hidden(), got)
        pg.evaluate("ACT.delete('backup'); renderActivity()")
        check("the last job ends: the indicator hides again", hidden() and not rows())
        check("no script errors", not errs, errs[:2])
        b.close()
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
