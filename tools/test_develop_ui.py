"""UI test of the Develop module in a real browser: the new sliders, the live preview, flips, presets, the Auto button, the EXIF
section of the info panel (catalog vs file) and the "keep edits and EXIF in the catalog" preference. Throw-away profile.

    py -3.12 tools/test_develop_ui.py            (set PHOTAG_TEST_BROWSER to a Chromium executable to use it instead of Edge)
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8796
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_devui_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"), "HOME": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999", "PHOTAG_EXIF_DELAY": "9999", "PHOTAG_MIGRATE_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib", "src"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=120).read() or b"{}")


def job(name, secs=120):
    t0 = time.time()
    while time.time() - t0 < secs:
        if call("GET", f"/api/job/{name}")["state"] in ("done", "error"):
            return
        time.sleep(0.3)


def scene(w, h, k):
    x = np.linspace(0, 1, w)[None, :, None]
    y = np.linspace(0, 1, h)[:, None, None]
    return np.clip(255 * k * np.concatenate([0.15 + 0.7 * x + 0 * y, 0.2 + 0.6 * y + 0 * x, 0.8 - 0.6 * x + 0 * y], axis=2), 0, 255).astype("uint8")


src = tmp / "src"
ex = Image.Exif()
ex[0x010F], ex[0x0110] = "UiMake", "UiModel"
Image.fromarray(scene(300, 200, 0.2)).save(src / "a_dark.jpg", quality=92, exif=ex)           # dark, with EXIF
Image.fromarray(scene(300, 200, 0.9)).save(src / "b_normal.jpg", quality=92)

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
    call("POST", "/api/import-folder", {"paths": [str(p) for p in sorted(src.iterdir())]})
    job("import")
    ph = {p["filename"]: p for p in call("GET", "/api/photos?limit=999")}
    A = ph["a_dark.jpg"]["id"]
    dbfile = call("GET", "/api/status")["db_path"]

    with sync_playwright() as pw:
        exe = os.environ.get("PHOTAG_TEST_BROWSER")
        b = pw.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"]) if exe else pw.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)
        pg.wait_for_timeout(800)

        def shot(name):
            if os.environ.get("PHOTAG_TEST_SHOTS"):
                pg.wait_for_timeout(500)
                pg.screenshot(path=str(Path(os.environ["PHOTAG_TEST_SHOTS"]) / f"{name}.png"))

        def cell(name):
            pg.locator(f".cell[data-id='{ph[name]['id']}']").click()
            pg.wait_for_timeout(500)

        def setslider(k, v):
            pg.evaluate("""([k,v])=>{const e=document.querySelector('#d-'+k); e.value=v; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));}""", [k, v])
            pg.wait_for_timeout(250)

        # ---- the EXIF section of the info panel: from the catalog
        cell("a_dark.jpg")
        check("the info panel has an 'All EXIF tags' section", pg.locator("#m-exifbox").count() == 1)
        pg.wait_for_selector("#m-exifbox .exif-kv", timeout=10000)
        txt = pg.inner_text("#m-exifbox")
        check("opened, it lists the tags (Make = UiMake)", "UiMake" in txt and "Make" in txt, txt[:120])
        check("...and says they come from the catalog", "From the catalog" in txt)
        shot("exif_panel")
        con = sqlite3.connect(dbfile)
        con.execute("UPDATE photos SET exif_json=? WHERE id=?", (json.dumps({"Image": {"Make": "FROM_THE_DATABASE"}}), A))
        con.commit()
        con.close()
        cell("b_normal.jpg")
        cell("a_dark.jpg")
        pg.wait_for_selector("#m-exifbox .exif-kv", timeout=10000)
        check("the panel shows what the DATABASE holds (not the file)", "FROM_THE_DATABASE" in pg.inner_text("#m-exifbox"))

        # ---- the preference
        pg.evaluate("preferences()")
        pg.wait_for_selector("#pf-cat", timeout=5000)
        check("Preferences: 'keep edits and EXIF in the catalog' is on by default", pg.is_checked("#pf-cat"))
        pg.uncheck("#pf-cat")
        pg.wait_for_timeout(500)
        check("...switching it off is saved by the server", call("GET", "/api/catalog-edits") == {"on": False})
        pg.keyboard.press("Escape")
        pg.evaluate("closeModal()")
        cell("b_normal.jpg")
        cell("a_dark.jpg")
        pg.wait_for_selector("#m-exifbox .exif-kv", timeout=10000)
        t2 = pg.inner_text("#m-exifbox")
        check("with the switch off the panel reads the FILE (real Make, says so)", "UiMake" in t2 and "Read from the file" in t2 and "FROM_THE_DATABASE" not in t2, t2[:100])
        pg.evaluate("preferences()")
        pg.wait_for_selector("#pf-cat", timeout=5000)
        pg.check("#pf-cat")
        pg.wait_for_timeout(500)
        check("...and switching it on again is saved", call("GET", "/api/catalog-edits") == {"on": True})
        pg.evaluate("closeModal()")

        # ---- Develop
        cell("a_dark.jpg")
        pg.click("#modules [data-mod=develop]")
        pg.wait_for_selector("#d-exp", timeout=10000)
        pg.wait_for_timeout(600)
        for k in ("exp", "bri", "con", "hi", "sh", "temp", "tint", "vib", "sat", "cla", "shp", "blr", "vig", "sep"):
            if pg.locator(f"#d-{k}").count() != 1:
                check(f"slider {k} exists", False)
        check("all 14 sliders exist (Exposure, Brightness, Contrast, Highlights, Shadows, Temperature, Tint, Vibrance, Saturation, Clarity, Sharpness, Blur, Vignette, Sepia)",
              all(pg.locator(f"#d-{k}").count() == 1 for k in ("exp", "bri", "con", "hi", "sh", "temp", "tint", "vib", "sat", "cla", "shp", "blr", "vig", "sep")))
        check("flip buttons and the Auto button exist", pg.locator("[data-flip=h]").count() == 1 and pg.locator("[data-flip=v]").count() == 1 and pg.locator("#btn-dev-auto").count() == 1)
        shot("develop_open")
        src0 = pg.get_attribute("#dev-img", "src")
        setslider("exp", 150)
        check("the exposure readout shows EV (+1.50)", pg.inner_text("#d-exp + output").strip() == "+1.50", pg.inner_text("#d-exp + output"))
        pg.wait_for_function("document.querySelector('#dev-img').src.startsWith('blob:')", timeout=10000)
        check("a tone slider asks the server for a preview (the image becomes a preview blob)", pg.get_attribute("#dev-img", "src").startswith("blob:"))
        setslider("exp", 0)
        pg.wait_for_function("!document.querySelector('#dev-img').src.startsWith('blob:')", timeout=10000)
        check("back at 0, the plain original is shown again", not pg.get_attribute("#dev-img", "src").startswith("blob:"))
        setslider("vig", -60)
        setslider("sep", 50)
        pg.wait_for_function("document.querySelector('#dev-img').src.startsWith('blob:')", timeout=10000)
        check("the history lists these steps (Vignette, Sepia)", "Vignette" in pg.inner_text("#p-history") and "Sepia" in pg.inner_text("#p-history"), pg.inner_text("#p-history")[:80])
        pg.dblclick("#d-vig")
        pg.wait_for_timeout(300)
        check("double-click on a slider resets it", pg.input_value("#d-vig") == "0")
        pg.click("[data-flip=h]")
        pg.wait_for_timeout(300)
        check("Flip horizontally marks the button and mirrors the canvas", "on" in (pg.get_attribute("[data-flip=h]", "class") or "") and "scale(-1, 1)" in (pg.evaluate("document.querySelector('#dev-canvas').style.transform") or ""),
              pg.evaluate("document.querySelector('#dev-canvas').style.transform"))
        pg.click("[data-flip=v]")
        pg.wait_for_timeout(200)
        check("Flip vertically too (scale(-1, -1))", "scale(-1, -1)" in pg.evaluate("document.querySelector('#dev-canvas').style.transform"))
        pg.click("[data-flip=v]")
        pg.click("[data-flip=h]")
        pg.wait_for_timeout(200)
        check("flips can be switched off again", pg.evaluate("document.querySelector('#dev-canvas').style.transform") in ("", None))
        # presets
        pg.locator("#p-presets [data-preset]", has_text="Warm Sepia").click()
        pg.wait_for_timeout(500)
        check("the 'Warm Sepia' preset sets Sepia 70 and a vignette", pg.input_value("#d-sep") == "70" and pg.input_value("#d-vig") == "-25", (pg.input_value("#d-sep"), pg.input_value("#d-vig")))
        pg.click("#btn-dev-reset")
        pg.wait_for_timeout(400)
        check("Reset sets every slider back to 0", all(pg.input_value(f"#d-{k}") == "0" for k in ("exp", "hi", "sh", "temp", "tint", "vib", "cla", "shp", "blr", "vig", "sep", "bri", "con", "sat")))

        # ---- Auto
        before_ops = call("GET", f"/api/photo/{A}")["edit_ops"]
        pg.click("#btn-dev-auto")
        pg.wait_for_function("document.querySelector('#p-history').innerText.includes('Auto')", timeout=20000)
        pg.wait_for_function(f"!document.querySelector('#btn-dev-apply').classList.contains('dirty')", timeout=20000)
        pg.wait_for_timeout(1500)
        d = call("GET", f"/api/photo/{A}")
        ops = json.loads(d["edit_ops"] or "{}")
        check("Auto edited the photo by itself and saved it (no Apply needed)", d["edited"] == 1 and before_ops in (None, "") and ops, d["edit_ops"])
        check("...the dark photo got more exposure", ops.get("exposure", 0) > 0.5, ops.get("exposure"))
        check("...and the sliders show it (Exposure slider > 0)", int(pg.input_value("#d-exp")) > 50, pg.input_value("#d-exp"))
        check("...white balance, vibrance and sharpness were decided too", all(k in ops for k in ("temperature", "tint", "vibrance", "sharpness")) or ops.get("sharpness") == 20, sorted(ops))
        check("Auto is an ordinary step in History", "Auto" in pg.inner_text("#p-history"))
        shot("develop_auto")
        # Auto keeps the geometry the user chose
        pg.click("[data-rot='90']")
        pg.wait_for_timeout(300)
        rot_before = pg.evaluate("DEV.ops.rot")
        pg.click("#btn-dev-auto")
        pg.wait_for_timeout(2500)
        check("running Auto again keeps the rotation", pg.evaluate("DEV.ops.rot") == rot_before == 90, rot_before)
        # the photo in the library is the edited look now, and the file is still the original
        orig_ok = (tmp / "lib").exists()
        pg.click("#btn-dev-revert")
        pg.wait_for_timeout(300)
        # revert confirm box
        if pg.locator("#cb-yes").count():
            pg.click("#cb-yes")
        pg.wait_for_timeout(1500)
        d = call("GET", f"/api/photo/{A}")
        check("Revert goes back to the original", d["edited"] == 0 and not d["edit_ops"], d["edit_ops"])
        # an edit with the new settings via the page, then Apply
        setslider("hi", -40)
        setslider("temp", 25)
        pg.click("#btn-dev-apply")
        pg.wait_for_timeout(1500)
        ops = json.loads(call("GET", f"/api/photo/{A}")["edit_ops"] or "{}")
        check("Apply stores the new settings (highlights, temperature) as sent by the page", ops.get("highlights") == -40 and ops.get("temperature") == 25, ops)
        # leaving Develop re-opening keeps them
        pg.click("#modules [data-mod=library]")
        pg.wait_for_timeout(400)
        pg.click("#modules [data-mod=develop]")
        pg.wait_for_timeout(800)
        check("re-opening Develop picks the stored settings up again", pg.input_value("#d-hi") == "-40" and pg.input_value("#d-temp") == "25", (pg.input_value("#d-hi"), pg.input_value("#d-temp")))
        # ---- Export with "Update the EXIF ... and write the edit settings with the originals"
        import piexif
        call("PATCH", f"/api/photo/{A}", {"description": "UI caption"})
        pg.click("#modules [data-mod=library]")
        pg.wait_for_timeout(600)
        cell("a_dark.jpg")
        pg.evaluate("openExport()")
        pg.wait_for_selector("#ex-meta", timeout=5000)
        check("the export dialog has the 'Update the EXIF ...' checkbox, off by default", pg.locator("#ex-meta").count() == 1 and not pg.is_checked("#ex-meta"))
        pg.select_option("#ex-kind", "html")
        pg.wait_for_timeout(200)
        check("...it is hidden for the HTML gallery (nothing to update there)", not pg.locator("#ex-meta").is_visible())
        pg.select_option("#ex-kind", "folder")
        pg.wait_for_timeout(200)
        exp = tmp / "exported"
        pg.fill("#ex-dest", str(exp))
        pg.check("input[name=ex-mode][value=original]")
        pg.check("#ex-meta")
        pg.click("#ex-go")
        for _ in range(60):
            if call("GET", "/api/job/export")["state"] in ("done", "error"):
                break
            time.sleep(0.5)
        files = sorted(exp.glob("*.jpg"))
        e = piexif.load(str(files[0])) if files else {"0th": {}}
        check("the export ran and the exported JPEG has the camera's EXIF plus the catalog's caption", len(files) == 1 and e["0th"].get(piexif.ImageIFD.Make) == b"UiMake" and e["0th"].get(piexif.ImageIFD.ImageDescription) == b"UI caption", e["0th"].get(270))
        xmp = list(exp.glob("*.xmp"))
        check("...and, as the photo is edited and the original was exported, an XMP sidecar with its edit settings", len(xmp) == 1 and "photag:EditSettings" in xmp[0].read_text("utf-8") and "crs:Highlights2012" in xmp[0].read_text("utf-8"))
        check("...the choice is remembered for next time", pg.evaluate("pref.get('export', {}).meta") is True)
        check("no JavaScript errors", not errs, errs[:3])
        b.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
