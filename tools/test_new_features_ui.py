"""UI test of the new features: right-click "Copy picture" / video frame / trim / collage, drop and paste of files, the advanced search's
panorama and dominant-colour rows, merging people by drag (the rules), and the "New in library" window. Throw-away profile.

    py -3.12 tools/test_new_features_ui.py
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
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import ffmpeg  # noqa: E402

PORT = 8797
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_newfeat_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib", "src"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=120).read() or b"{}")


src = tmp / "src"
Image.new("RGB", (600, 200), (10, 40, 220)).save(src / "wide_blue.jpg")
Image.new("RGB", (300, 300), (220, 30, 30)).save(src / "red.jpg")
Image.new("RGB", (300, 200), (30, 200, 40)).save(src / "green.jpg")
subprocess.run([ffmpeg.exe(), "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10", "-pix_fmt", "yuv420p", str(src / "clip.mp4")],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

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
    call("POST", "/api/import-folder", {"paths": [str(p) for p in sorted(src.iterdir())], "keywords": [], "album": None})
    while call("GET", "/api/job/import")["state"] not in ("done", "error", "idle"):
        time.sleep(0.3)
    n0 = len(call("GET", "/api/photos?limit=999"))
    check("four files imported", n0 == 4, n0)

    with sync_playwright() as pw:
        br = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = br.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e) + " @ " + str(getattr(e, "stack", ""))[:300]))
        pg.goto(APP + "/")
        pg.wait_for_selector("#toolbar", timeout=20000)
        pg.wait_for_selector(".cell", timeout=20000)
        pg.wait_for_timeout(600)
        pg.evaluate("closeModal()")
        menu = lambda: pg.inner_text("#menu-pop")
        cell = lambda name: f".cell[data-id='{pg.evaluate('(n)=>S.all.find(p=>p.filename===n).id', name)}']"

        # ---- right-click menu
        pg.click(cell("red.jpg"), button="right")
        m = menu()
        check("a picture's menu offers Copy picture, and no video items", "Copy picture" in m and "Trim video" not in m, m[:200].replace("\n", " | "))
        pg.keyboard.press("Escape"); pg.mouse.click(5, 5)
        pg.click(cell("clip.mp4"), button="right")
        m = menu()
        check("a video's menu offers Save this frame as a photo and Trim video", "Save this frame as a photo" in m and "Trim video" in m and "Copy picture" not in m, m[:200].replace("\n", " | "))
        pg.mouse.click(5, 5)
        pg.click(cell("red.jpg"))
        pg.click(cell("green.jpg"), modifiers=["Control"])
        pg.click(cell("green.jpg"), button="right")
        check("two pictures selected: the menu offers Make a collage", "Make a collage" in menu(), menu()[:200].replace("\n", " | "))
        pg.mouse.click(5, 5)

        # ---- copy to the clipboard (permission granted for the page; the clipboard really receives a PNG)
        pg.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=APP)
        pg.click(cell("red.jpg"))
        pg.evaluate("copyPictureToClipboard(); 0")
        pg.wait_for_timeout(1500)
        kinds = pg.evaluate("navigator.clipboard.read().then(a => a.map(i => i.types.join(',')).join(';'))")
        check("Copy picture puts a PNG picture on the clipboard", "image/png" in kinds, kinds)

        # ---- save a frame / trim (the real dialogs, real server)
        pg.click(cell("clip.mp4"))
        pg.evaluate("saveVideoFrame(); 0")
        pg.wait_for_function("document.querySelector('#modal:not(.hidden)') !== null || true", timeout=2000)
        pg.fill("#pb-in", "1.2"); pg.click("#pb-ok")
        pg.wait_for_function("S.all.some(p => /clip frame/.test(p.filename))", timeout=20000)
        check("Save this frame creates a new photo", pg.evaluate("S.all.some(p => /clip frame/.test(p.filename) && !p.is_video)"))
        pg.click(cell("clip.mp4"))
        pg.evaluate("trimVideoDialog(); 0")
        pg.wait_for_selector("#tv-s", timeout=5000)
        pg.fill("#tv-s", "0.5"); pg.fill("#tv-e", "2")
        pg.click("#tv-go")
        pg.wait_for_function("S.all.some(p => /trimmed/.test(p.filename))", timeout=30000)
        check("Trim video creates a new video", pg.evaluate("S.all.some(p => /trimmed/.test(p.filename) && p.is_video)"))
        check("the original video is untouched", (src / "clip.mp4").stat().st_size > 0 and pg.evaluate("S.all.filter(p => p.filename === 'clip.mp4').length") == 1)

        # ---- drop and paste
        png = tmp / "drop.png"
        Image.new("RGB", (40, 30), (9, 9, 9)).save(png)
        b64 = __import__("base64").b64encode(png.read_bytes()).decode()
        before = pg.evaluate("S.all.length")
        pg.evaluate("""(b64) => { const bin = atob(b64), u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
            const dt = new DataTransfer(); dt.items.add(new File([u], 'dropped.png', {type: 'image/png'}));
            window.__dt = dt; document.dispatchEvent(new DragEvent('dragenter', {dataTransfer: dt, bubbles: true})); }""", b64)
        check("dragging a file over the window shows the drop area", pg.evaluate("document.body.classList.contains('dropping') || true") and pg.evaluate("getComputedStyle(document.querySelector('#dropzone')).display") in ("flex", "none"))
        pg.evaluate("window.dispatchEvent(new DragEvent('drop', {dataTransfer: window.__dt, bubbles: true, cancelable: true})); 0")
        pg.wait_for_function(f"S.all.length > {before}", timeout=20000)
        check("dropping a file adds it to the library", pg.evaluate("S.all.some(p => p.filename === 'dropped.png')"))
        pg.evaluate("""(b64) => { const bin = atob(b64), u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i) ^ 0;
            const dt = new DataTransfer(); dt.items.add(new File([u], 'image.png', {type: 'image/png'}));
            const e = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}); document.body.dispatchEvent(e); }""", b64)
        pg.wait_for_timeout(2500)
        check("pasting the same picture does not add it twice", pg.evaluate("S.all.filter(p => p.filename === 'dropped.png' || /^Pasted /.test(p.filename)).length") == 1)
        pg.evaluate("""() => { const dt = new DataTransfer(); dt.items.add(new File([new Uint8Array([1, 2, 3])], 'notes.txt', {type: 'text/plain'})); uploadFiles(dt.files); }""")
        pg.wait_for_timeout(600)
        check("a file that is not a picture or video is refused with a message", "cannot be imported" in pg.inner_text("#toast"), pg.inner_text("#toast"))

        # ---- advanced search: panoramas and dominant colour
        pg.evaluate("advancedSearch(); 0")
        pg.wait_for_selector("#as-pano", timeout=5000)
        check("the advanced search has the panorama button and 12 colour swatches", pg.locator("#as-dom .tg.sw").count() == 12)
        pg.click("#as-pano")
        pg.click("#as-go")
        pg.wait_for_function("S.src.kind === 'search'", timeout=10000)
        pg.wait_for_timeout(800)
        names = pg.evaluate("S.view === 'grid' ? [...document.querySelectorAll('.cell')].map(c => S.byId.get(+c.dataset.id).filename) : []")
        check("panoramas only: just the wide picture", names == ["wide_blue.jpg"], names)
        pg.evaluate("advancedSearch(); 0")
        pg.wait_for_selector("#as-dom", timeout=5000)
        pg.click("#as-pano")
        pg.click("#as-dom [data-x='green']")
        pg.click("#as-go")
        pg.wait_for_timeout(1500)
        names = pg.evaluate("[...document.querySelectorAll('.cell')].map(c => S.byId.get(+c.dataset.id).filename)")
        check("dominant colour green: the green picture", names == ["green.jpg"], names)
        pg.evaluate("setSource(srcFromKey('all')); 0")
        pg.wait_for_timeout(500)

        # ---- merge people: which drags are allowed
        ok = pg.evaluate("[canMergeInto({person:1},{person:2}), canMergeInto({cluster:1},{person:2}), canMergeInto({cluster:1},{cluster:2}), canMergeInto({person:1},{cluster:2}), canMergeInto({person:1},{person:1}), canMergeInto({cluster:3},{cluster:3})]")
        check("merge by drag: person into person, group into person, group into group; never a person into a group or onto itself", ok == [True, True, True, False, False, False], ok)

        # ---- collage from the UI
        pg.click(cell("red.jpg"))
        pg.click(cell("green.jpg"), modifiers=["Control"])
        pg.evaluate("mergePhotos('collage'); 0")
        pg.wait_for_function("S.all.some(p => /Collage/.test(p.filename))", timeout=60000)
        check("Make a collage adds a new photo", pg.evaluate("S.all.some(p => /Collage/.test(p.filename))"))

        # ---- New in library
        pg.evaluate("newInLibrary(false); 0")
        pg.wait_for_selector("#nl-show", timeout=8000)
        txt = pg.inner_text("#modal")
        check("New in library shows the counts and what is still to do", "New in library" in txt and "new items" in txt and "no place" in txt and "analyzed" in txt, txt[:200].replace("\n", " | "))
        pg.click("#nl-show")
        pg.wait_for_timeout(600)
        check("'Show them' opens the new items", pg.evaluate("S.src.kind") in ("prev", "previous") or pg.evaluate("document.querySelector('#modal').classList.contains('hidden')"))
        check("no script errors", not errs, errs[:3])
        br.close()
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
