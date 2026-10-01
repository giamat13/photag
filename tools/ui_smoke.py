"""UI smoke test of the language handling: starts the app server from source with a throw-away profile (never touches your
real library or settings), imports the sample photos of tools/sandbox/photos, and drives the UI in Edge with Playwright.

    py -3.12 -m pip install playwright          (uses the installed Edge, nothing to download)
    py -3.12 tools/ui_smoke.py

Checks: English is the default (LTR, no Hebrew anywhere, in the page, the menus and every dialog), Hebrew switches to RTL
with Hebrew texts, and another language (Japanese) shows no Hebrew either. Exit code 0 = all passed.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8775
APP = f"http://127.0.0.1:{PORT}"
HEB = re.compile(r"[֐-׿]")
tmp = Path(tempfile.mkdtemp(prefix="photag_ui_smoke_"))
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
    # an empty library: the main window shows the empty state, it must not jump to the Import screen by itself
    with sync_playwright() as pw0:
        b0 = pw0.chromium.launch(channel="msedge", headless=True)
        p0 = b0.new_page(viewport={"width": 1400, "height": 900})
        p0.goto(APP + "/")
        p0.wait_for_selector("#v-empty:not(.hidden)", timeout=20000)
        p0.wait_for_timeout(1500)
        check("empty library: the empty-state screen is shown", p0.evaluate("!document.querySelector('#v-empty').classList.contains('hidden')"))
        check("empty library: the Import screen does NOT open by itself", p0.evaluate("document.querySelector('#import').classList.contains('hidden')"))
        empty_text = p0.evaluate("document.querySelector('#v-empty').innerText")
        check("empty library: the empty state offers an Import button", "Import" in empty_text, empty_text[:80].replace(chr(10), " "))
        b0.close()
    files = [str(p) for p in sorted((ROOT / "tools" / "sandbox" / "photos").iterdir())]
    call("POST", "/api/import-folder", {"paths": files, "keywords": [], "album": None})
    while call("GET", "/api/job/import")["state"] not in ("done", "error"):
        time.sleep(0.3)
    ph = {p["filename"]: p for p in call("GET", "/api/photos?limit=99")}
    check("sample photos imported", len(ph) == len(files), len(ph))

    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="msedge", headless=True)

        def open_page(lang):
            pg = b.new_page(viewport={"width": 1500, "height": 950})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            if lang:
                pg.add_init_script(f"localStorage.setItem('pm.lang', '\"{lang}\"')")
            pg.goto(APP + "/")
            pg.wait_for_selector(".cell", timeout=20000)
            return pg, errs

        # ------------------------------------------------------------ English (default, nothing saved)
        pg, errs = open_page(None)
        ev = pg.evaluate
        check("default language: English, left to right", ev("document.documentElement.lang") == "en" and ev("document.documentElement.dir") == "ltr")
        body = ev("document.body.innerText")
        check("the main window has no Hebrew text", not HEB.search(body), HEB.findall(body)[:5])
        labels = ev("MENUS.flatMap(m => [m[0], ...m[1].filter(x => Array.isArray(x)).map(x => x[0])])")
        check("menus have no Hebrew", not any(HEB.search(l) for l in labels), [l for l in labels if HEB.search(l)][:3])
        check("one 'Import…' entry in the File menu (not three)", sum(1 for l in labels if l.startswith("Import")) == 1, [l for l in labels if "mport" in l])
        tips = ev("[...document.querySelectorAll('[title],[placeholder]')].map(e => e.title || e.placeholder)")
        check("tooltips and placeholders have no Hebrew", not any(HEB.search(x) for x in tips), [x for x in tips if HEB.search(x)][:3])
        img = ph["big_q97_exif.jpg"]["id"]
        dialogs = {
            "import screen": "openImport('folder')",
            "backup dialog": "backupDialog()",
            "compress dialog": f"compressDialog({img})",
            "language dialog": "languageDialog()",
            "catalog settings": "catalogSettings()",
            "preferences": "preferences()",
            "AI settings": "aiSettings()",
            "about": "[...MENUS.flatMap(m => m[1]).filter(x => Array.isArray(x))].find(x => /^About/.test(x[0]))[2]()",
        }
        for name, js in dialogs.items():
            try:
                ev(f"(()=>{{ closeModal(); const r = ({js}); return 1; }})()")
                pg.wait_for_timeout(700)
                txt = ev("document.querySelector('#modal:not(.hidden) #modal-box')?.innerText || document.querySelector('#import:not(.hidden)')?.innerText || ''")
                bad = HEB.findall(txt.replace("עברית", ""))     # the language list shows each language in its own script
                check(f"{name}: shown, no Hebrew", bool(txt.strip()) and not bad, bad[:5] if bad else f"{len(txt)} chars")
            except Exception as e:
                check(f"{name}: shown", False, str(e)[:120])
            ev("closeModal(); document.querySelector('#import').classList.add('hidden')")
        ev("(()=>{ closeModal(); IM.mode = 'zip'; IM.zips = [{path:'C:\\t\\takeout-1-001.zip', name:'takeout-1-001.zip', bytes: 5e9}, {path:'C:\\t\\takeout-1-003.zip', name:'takeout-1-003.zip', bytes: 3e9}]; IM.zipMissing = [2]; IM.zipFound = 1; openImport('zip'); })()")
        pg.wait_for_timeout(500)
        zt = ev("document.querySelector('#import').innerText")
        check("Takeout screen lists the parts, the total, the added parts and the missing-part warning (English)",
              "takeout-1-001.zip" in zt and "2 ZIP files" in zt and "1 more parts" in zt and "Part 002 is missing" in zt and not HEB.search(zt), zt[:100].replace(chr(10), " "))
        check("with parts chosen the Import button is enabled", not ev("document.querySelector('#im-go').disabled"))
        check("sizes are shown in GB / TB, not 19000 MB", ev("[fsize(19e9), fsize(5e11), fsize(2e12), fsize(5e8), fsize(2e4)]") == ["17.7 GB", "465.7 GB", "1.82 TB", "476.8 MB", "20 KB"] and "GB" in zt, ev("[fsize(19e9), fsize(5e11), fsize(2e12)]"))
        # the file dialog says C:/x/a.zip and the server C:\x\a.zip: they are the SAME file (a part must not be listed twice)
        check("a ZIP chosen in the dialog and the same one found by the server are one entry", ev(r"zkey('C:/Users/me/takeout-1-001.zip') === zkey('c:\\users\\me\\Takeout-1-001.ZIP')"))
        # a part added by mistake can be removed again; "Remove all" empties the list; the missing-part warning follows
        pg.click('[data-zrm="1"]')
        pg.wait_for_timeout(300)
        zt = ev("document.querySelector('#import').innerText")
        check("removing one part: it disappears, the others stay, the missing-part warning goes away", "takeout-1-003.zip" not in zt and "takeout-1-001.zip" in zt and "Part 002 is missing" not in zt and "2 ZIP files" not in zt, zt[:80].replace(chr(10), " "))
        ev("IM.zips.push({path:'C:\\t\\takeout-1-003.zip', name:'takeout-1-003.zip', bytes: 3e9}); IM.zipMissing = zipGaps(IM.zips); renderImport()")
        check("zipGaps finds part 002 missing between 001 and 003", ev("zipGaps(IM.zips)") == [2])
        pg.click("[data-zclear]")
        pg.wait_for_timeout(300)
        check("Remove all: the list is empty and Import is disabled", ev("IM.zips.length") == 0 and ev("document.querySelector('#im-go').disabled"))
        ev("document.querySelector('#import').classList.add('hidden')")
        ev("(()=>{ setView('grid'); S.sel.clear(); S.sel.add(%d); S.act=%d; onSelChange(); trashSelected(); })()" % (img, img))
        pg.wait_for_selector("#cb-yes", timeout=5000)
        txt = ev("document.querySelector('#modal-box').innerText")
        check("delete confirmation in English", "trash" in txt.lower() and not HEB.search(txt), txt[:80].replace("\n", " "))
        ev("closeModal()")
        pg.click("#toolbar [data-view=map]")
        pg.wait_for_timeout(1200)
        mapinfo = ev("document.querySelector('#map-info')?.innerText || ''")
        check("map view: toolbar text in English", bool(mapinfo) and not HEB.search(mapinfo), mapinfo)
        check("no JavaScript errors (English)", not errs, errs[:2])
        pg.close()

        # ------------------------------------------------------------ Hebrew
        pg, errs = open_page("he")
        ev = pg.evaluate
        check("Hebrew: right to left", ev("document.documentElement.lang") == "he" and ev("document.documentElement.dir") == "rtl")
        labels = ev("MENUS.map(m => m[0])")
        check("Hebrew menus", "קובץ" in labels, labels[:4])
        body = ev("document.body.innerText")
        check("Hebrew main window has Hebrew text", len(HEB.findall(body)) > 40, len(HEB.findall(body)))
        ev("backupDialog()")
        pg.wait_for_timeout(700)
        txt = ev("document.querySelector('#modal-box').innerText")
        check("Hebrew backup dialog", "גיבוי" in txt, txt[:50].replace("\n", " "))
        check("no JavaScript errors (Hebrew)", not errs, errs[:2])
        pg.close()

        # ------------------------------------------------------------ another language
        pg, errs = open_page("ja")
        ev = pg.evaluate
        body = ev("document.body.innerText")
        check("Japanese: no Hebrew leaks into the window", not HEB.search(body), HEB.findall(body)[:5])
        ev("backupDialog()")
        pg.wait_for_timeout(700)
        txt = ev("document.querySelector('#modal-box').innerText")
        check("Japanese backup dialog: translated, no Hebrew", not HEB.search(txt) and re.search(r"[぀-ヿ一-鿿]", txt), txt[:40].replace("\n", " "))
        check("no JavaScript errors (Japanese)", not errs, errs[:2])
        pg.close()
        b.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=15)
    except Exception:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
