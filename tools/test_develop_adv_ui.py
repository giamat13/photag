"""UI test of the advanced Develop tools (issue #6, batch 1) in a real browser: whites / blacks / texture / dehaze, vignette handles,
grain, tone curve editor, colour mixer, black & white mix, colour grading, calibration, white balance preset and eyedropper,
saving and reloading. Throw-away profile.

    py -3.12 tools/test_develop_adv_ui.py            (set PHOTAG_TEST_BROWSER to a Chromium executable to use it instead of Edge)
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
PORT = 8799
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_devadv_test_"))
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

        def setn(path, v):
            pg.evaluate("""([id,v])=>{const e=document.getElementById(id); e.value=v; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));}""", [path, v])
            pg.wait_for_timeout(200)

        def saved():
            return json.loads(call("GET", "/api/photo/%d" % ph["b_normal.jpg"]["id"])["edit_ops"] or "{}")

        def apply_():
            pg.click("#btn-dev-apply")
            pg.wait_for_function("!document.querySelector('#btn-dev-apply').classList.contains('dirty')", timeout=20000)
            pg.wait_for_timeout(600)

        cell("b_normal.jpg")
        pg.click("#modules [data-mod=develop]")
        pg.wait_for_selector("#d-exp", timeout=10000)
        pg.wait_for_timeout(600)
        new = ("wht", "blk", "tex", "dhz", "vgm", "vgf", "vgr", "vgh", "grn", "grs", "grr")
        check("the new basic sliders exist (Whites, Blacks, Texture, Dehaze, vignette handles, grain)", all(pg.locator(f"#d-{k}").count() == 1 for k in new))
        check("the grain size / roughness and vignette midpoint / feather rest at 25 / 50 / 50 / 50",
              [pg.input_value(f"#d-{k}") for k in ("grs", "grr", "vgm", "vgf")] == ["25", "50", "50", "50"])
        check("the four extra panels exist (Tone Curve, Color Mixer, Color Grading, Calibration)", all(pg.locator(f"#right [data-p={p}]").count() == 1 for p in ("curve", "mixer", "grading", "calib")))
        shot("adv_open")
        # whites / blacks / texture / dehaze / grain preview
        setslider("grs", 60)
        pg.wait_for_timeout(500)
        check("a resting grain amount (0) does not ask the server for a preview, whatever its size", not pg.get_attribute("#dev-img", "src").startswith("blob:"))
        check("the sharpening and noise-reduction sliders exist and rest at 50 / 25 / 0 and 0 / 0 / 50",
              [pg.input_value(f"#d-{k}") for k in ("shr", "shd", "shm", "nrl", "nrc", "nrd")] == ["50", "25", "0", "0", "0", "50"])
        setslider("shm", 60)
        pg.wait_for_timeout(400)
        check("sharpening masking alone (no sharpness) does not ask for a preview", not pg.get_attribute("#dev-img", "src").startswith("blob:"))
        for k, v in (("shp", 30), ("shr", 70), ("nrl", 40), ("nrc", 30), ("nrd", 80)):
            setslider(k, v)
        for k, v in (("wht", 40), ("blk", -30), ("tex", 50), ("dhz", 30), ("grn", 25)):
            setslider(k, v)
        pg.wait_for_function("document.querySelector('#dev-img').src.startsWith('blob:')", timeout=10000)
        check("the new tone sliders ask the server for a preview", pg.get_attribute("#dev-img", "src").startswith("blob:"))
        check("...and the readouts show the values (+40, -30)", pg.inner_text("#d-wht + output").strip() == "+40" and pg.inner_text("#d-blk + output").strip() == "-30")
        pg.dblclick("#d-grs")
        pg.wait_for_timeout(300)
        check("double-click on grain size goes back to its resting place (25), not 0", pg.input_value("#d-grs") == "25", pg.input_value("#d-grs"))
        # tone curve
        box = pg.locator("#curve-svg").bounding_box()
        pg.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.2)
        pg.wait_for_timeout(400)
        check("a click on the curve adds a point", pg.locator("#curve-svg circle").count() == 1)
        pg.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.2)
        pg.mouse.down()
        pg.mouse.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.1, steps=4)
        pg.mouse.up()
        pg.wait_for_timeout(400)
        cx = float(pg.get_attribute("#curve-svg circle", "cx")); cy = float(pg.get_attribute("#curve-svg circle", "cy"))
        check("dragging the point moves it (up and right)", cx > 100 and cy < 40, (cx, cy))
        check("the curve is a step in History", "Tone Curve" in pg.inner_text("#p-history"))
        pg.click("[data-cch=r]")
        pg.wait_for_timeout(200)
        check("the Red channel starts with a straight line (no points)", pg.locator("#curve-svg circle").count() == 0)
        pg.click("[data-cch=rgb]")
        setn("n-crp-3", 40)
        check("the parametric curve has its own sliders (Shadows +40)", pg.inner_text("#n-crp-3 + output").strip() == "+40")
        # colour mixer
        setn("n-mix-blue-0", 50)
        pg.click("[data-mix='1']")
        pg.wait_for_timeout(200)
        setn("n-mix-red-1", -100)
        pg.click("[data-mix='2']")
        pg.wait_for_timeout(200)
        setn("n-mix-green-2", 30)
        pg.click("[data-mix='0']")
        pg.wait_for_timeout(200)
        check("the mixer remembers each band and tab (Blue hue +50 is still there)", pg.input_value("#n-mix-blue-0") == "50")
        # colour grading
        setn("n-grd-shadows-0", 210)
        setn("n-grd-shadows-1", 40)
        setn("n-grd-high-0", 40)
        setn("n-grd-high-1", 30)
        setn("n-grd-balance", -20)
        check("the grading hue shows degrees (210°)", pg.inner_text("#n-grd-shadows-0 + output").strip() == "210°")
        # calibration
        setn("n-cal-shadow_tint", 30)
        setn("n-cal-red-1", 25)
        # white balance preset
        pg.select_option("select[data-wb]", index=3)
        pg.wait_for_timeout(300)
        check("the white balance preset 'Tungsten' sets Temperature -45", pg.input_value("#d-temp") == "-45", pg.input_value("#d-temp"))
        shot("adv_edited")
        apply_()
        o = saved()
        check("Apply saved the curve (one point, moved)", o.get("curve", {}).get("rgb") and len(o["curve"]["rgb"]) == 1 and o["curve"]["rgb"][0][0] > .5, o.get("curve"))
        check("...the parametric curve", o.get("curve_p") == [0, 0, 0, 40], o.get("curve_p"))
        check("...the mixer (red saturation -100, blue hue +50, green luminance +30)",
              o.get("mixer", {}).get("red", [0, 0, 0])[1] == -100 and o["mixer"]["blue"][0] == 50 and o["mixer"]["green"][2] == 30, o.get("mixer"))
        check("...the grading (shadows 210° 40%, highlights 40° 30%, balance -20)",
              o.get("grading", {}).get("shadows", [0, 0, 0])[:2] == [210, 40] and o["grading"]["high"][:2] == [40, 30] and o["grading"].get("balance") == -20, o.get("grading"))
        check("...the calibration", o.get("calib", {}).get("shadow_tint") == 30 and o["calib"]["red"][1] == 25, o.get("calib"))
        check("...and the plain ones (whites 40, blacks -30, texture 50, dehaze 30, grain 25, temperature -45)",
              (o.get("whites"), o.get("blacks"), o.get("texture"), o.get("dehaze"), o.get("grain"), o.get("temperature")) == (40, -30, 50, 30, 25, -45),
              (o.get("whites"), o.get("blacks"), o.get("texture"), o.get("dehaze"), o.get("grain"), o.get("temperature")))
        check("...sharpening (30, radius 70, masking 60) and noise reduction (luminance 40, colour 30, detail 80)",
              (o.get("sharpness"), o.get("sharp_radius"), o.get("sharp_mask"), o.get("nr_lum"), o.get("nr_color"), o.get("nr_detail")) == (30, 70, 60, 40, 30, 80),
              (o.get("sharpness"), o.get("sharp_radius"), o.get("sharp_mask"), o.get("nr_lum"), o.get("nr_color"), o.get("nr_detail")))
        check("...a resting grain size / vignette midpoint is not saved", "grain_size" not in o and "vignette_mid" not in o, sorted(o))
        # leave and come back: everything is read from the saved settings
        pg.click("#modules [data-mod=library]")
        pg.wait_for_timeout(300)
        cell("a_dark.jpg")
        cell("b_normal.jpg")
        pg.click("#modules [data-mod=develop]")
        pg.wait_for_selector("#d-wht", timeout=10000)
        pg.wait_for_timeout(700)
        check("opened again, the sliders show the saved values (whites 40, grain 25, mixer red saturation)", pg.input_value("#d-wht") == "40" and pg.input_value("#d-grn") == "25")
        pg.click("[data-mix='1']")
        pg.wait_for_timeout(200)
        check("...and the mixer / grading / curve too", pg.input_value("#n-mix-red-1") == "-100" and pg.input_value("#n-grd-shadows-1") == "40" and pg.locator("#curve-svg circle").count() == 1)
        # black & white mix
        pg.click("[data-gray='1']")
        pg.wait_for_timeout(300)
        check("with Black & White the Color Mixer becomes the black & white mix", pg.locator("#n-bwm-red").count() == 1 and pg.locator("#n-mix-red-1").count() == 0)
        setn("n-bwm-red", 60)
        apply_()
        check("...and its values are saved (red +60)", saved().get("bwmix", {}).get("red") == 60 and saved().get("grayscale") is True, saved().get("bwmix"))
        pg.click("[data-gray='0']")
        pg.wait_for_timeout(300)
        # eyedropper
        pg.click("#btn-dev-reset")
        pg.wait_for_timeout(300)
        check("Reset clears the curve, the mixer and the grading too", pg.input_value("#d-wht") == "0" and pg.locator("#curve-svg circle").count() == 0 and pg.input_value("#n-grd-shadows-1") == "0")
        pg.click("[data-t=wbpick]")
        pg.wait_for_timeout(400)
        check("the eyedropper turns on (crosshair)", pg.evaluate("document.querySelector('#v-develop').classList.contains('picking')"))
        bx = pg.locator("#dev-img").bounding_box()
        pg.mouse.click(bx["x"] + bx["width"] * 0.8, bx["y"] + bx["height"] * 0.5)
        pg.wait_for_timeout(500)
        tp, ti = int(pg.input_value("#d-temp")), int(pg.input_value("#d-tint"))
        check("a click on a coloured area sets Temperature and Tint to make it neutral", tp != 0 or ti != 0, (tp, ti))
        check("...and the eyedropper turns itself off", not pg.evaluate("document.querySelector('#v-develop').classList.contains('picking')"))
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
